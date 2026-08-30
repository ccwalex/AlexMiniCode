#!/usr/bin/env python3
"""Gen2 web GUI with sequential queue, tracked-folder registry contexts, file-tree injection, logs, and git."""
import argparse, json, os, signal, subprocess, sys, time, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
DEFAULT_MODEL='mini'; DEFAULT_EFFORT='l'; DEFAULT_MAX_TOKENS=16384
DEFAULT_SHELL="""Allow creating and editing project files.
Allow running Python scripts.
Do not allow deleting files unless explicitly requested.
Do not allow shell redirection for file writes."""
TERMINAL={'completed','failed','cancelled'}
EXCLUDE_DIRS={'.git','__pycache__','.ipynb_checkpoints','node_modules','.venv','venv','env','.pytest_cache','.mypy_cache'}
EXCLUDE_SUFFIXES={'.pyc','.pyo','.png','.jpg','.jpeg','.gif','.webp','.ico','.pdf','.zip','.tar','.gz','.7z','.mp4','.mov','.avi','.sqlite','.db','.parquet','.npy','.npz','.pt','.pth'}
def source_dir(): return Path(__file__).resolve().parent
def project_root(): return source_dir().parent
def modules_dir(): return source_dir()/'modules'
def worker_script(): return source_dir()/'run_gen2_job_worker.py'
def memory_dir(): return project_root()/'agent_memory'
def jobs_dir(): return memory_dir()/'jobs'
def queue_file(): return jobs_dir()/'queue.json'
def current_file(): return jobs_dir()/'current.json'
def now(): return datetime.now(timezone.utc).isoformat()
def ensure_module_path():
    for p in (source_dir(),modules_dir()):
        s=str(p)
        if s not in sys.path: sys.path.insert(0,s)
def read_json(path,default=None):
    p=Path(path)
    if not p.exists(): return default
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return default
def write_json(path,obj):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix(p.suffix+'.tmp'); tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8'); tmp.replace(p)
def write_text(path,text):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(str(text),encoding='utf-8')
def tail(path,n=50000):
    p=Path(path)
    if not p.exists(): return ''
    t=p.read_text(encoding='utf-8',errors='replace')
    return t if len(t)<=n else '[TRUNCATED]\n'+t[-n:]
def resolve_project_path(path):
    root=project_root().resolve(); p=(root/str(path)).resolve()
    if p!=root and root not in p.parents: raise ValueError(f'path escapes project root: {path}')
    return p
def relpath(path):
    root=project_root().resolve(); p=Path(path).resolve()
    if p==root: return '.'
    if root not in p.parents: raise ValueError('outside project root')
    return p.relative_to(root).as_posix()
def ensure_storage():
    jobs_dir().mkdir(parents=True,exist_ok=True)
    if not isinstance(read_json(queue_file(),[]),list): write_json(queue_file(),[])
    if not isinstance(read_json(current_file(),{}),dict): write_json(current_file(),{})
def safe_job_id(j):
    if not isinstance(j,str) or not j.strip(): raise ValueError('empty job id')
    j=j.strip(); allowed=set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.')
    if any(c not in allowed for c in j): raise ValueError('invalid job id')
    return j
def job_dir(j):
    j=safe_job_id(j); root=jobs_dir().resolve(); p=(root/j).resolve()
    if p!=root and root not in p.parents: raise ValueError('job path escape')
    return p
def load_queue():
    q=read_json(queue_file(),[])
    if not isinstance(q,list): q=[]
    seen=set(); out=[]
    for x in q:
        try: x=safe_job_id(x)
        except Exception: continue
        if x not in seen: out.append(x); seen.add(x)
    if out!=q: write_json(queue_file(),out)
    return out
def save_queue(q): write_json(queue_file(),q)
def load_current():
    c=read_json(current_file(),{})
    return c if isinstance(c,dict) else {}
def save_current(c): write_json(current_file(),c or {})
def update_status(j,**kw):
    p=job_dir(j)/'status.json'; st=read_json(p,{})
    if not isinstance(st,dict): st={}
    st.update(kw); st['updated_at']=now(); write_json(p,st); return st
def alive(pid):
    try: pid=int(pid)
    except Exception: return False
    if pid<=0: return False
    try: os.kill(pid,0); return True
    except ProcessLookupError: return False
    except PermissionError: return True
    except Exception: return False
# tracked folders / registry contexts
def metadata_path_for(folder): return 'agent_memory/meta/'+(str(folder).strip().replace('\\','/').strip('/').replace('/','_') or 'root')
def tf_normalize(row):
    row=row or {}
    folder=row.get('folder_path') or row.get('folder') or row.get('path') or ''
    return {'folder_path':folder,'code_type':row.get('code_type') or row.get('type') or '', 'metadata_path':row.get('metadata_path') or row.get('meta_path') or metadata_path_for(folder)}
def tracked_folders():
    ensure_module_path()
    try:
        from modules.track_folders import TrackFolders
        return {'success':True,'folders':[tf_normalize(x) for x in TrackFolders().get_all()]}
    except Exception as e: return {'success':False,'error':str(e),'folders':[]}
def add_tracked(data):
    ensure_module_path(); folder=str(data.get('folder_path','')).strip(); ctype=str(data.get('code_type','py')).strip() or 'py'
    if not folder: raise ValueError('folder_path required')
    resolve_project_path(folder)
    from modules.track_folders import TrackFolders
    tf=TrackFolders(); tf.add_folder(folder,ctype,data.get('metadata_path') or metadata_path_for(folder)); tf.save(); return tracked_folders()
def remove_tracked(data):
    ensure_module_path(); folder=str(data.get('folder_path','')).strip()
    if not folder: raise ValueError('folder_path required')
    from modules.track_folders import TrackFolders
    tf=TrackFolders(); removed=tf.remove_folder(folder); tf.save(); out=tracked_folders(); out['removed']=bool(removed); return out
def registry_contexts():
    out=[]
    for row in tracked_folders().get('folders',[]):
        mp=row.get('metadata_path') or metadata_path_for(row.get('folder_path','')); p=resolve_project_path(mp); files=[]
        if p.exists():
            if p.is_file(): files=[relpath(p)]
            else:
                for x in p.rglob('*'):
                    if x.is_file() and x.stat().st_size<=2_000_000: files.append(relpath(x))
        out.append({**row,'exists':p.exists(),'metadata_files':sorted(files),'file_count':len(files)})
    return out
REGISTRY_FILE_MAX_CHARS = 60000

def _parse_metadata_file_content(text):
    """Parse metadata file text as JSON when possible; otherwise return raw text."""
    try:
        return json.loads(str(text))
    except Exception:
        return str(text)

def _truncate_registry_content(content, max_chars=REGISTRY_FILE_MAX_CHARS):
    """Limit serialized registry content size for prompt injection."""
    if isinstance(content, (dict, list)):
        serialized = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    else:
        serialized = str(content)

    if len(serialized) <= max_chars:
        return content

    marker = "[TRUNCATED]"
    return serialized[: max_chars - len(marker)] + marker

def _source_path_from_metadata(meta_file_path, folder_path, metadata_root):
    """Best-effort source file path derived from metadata file location."""
    try:
        meta_rel = str(meta_file_path).replace("\\", "/")
        root_rel = str(metadata_root).replace("\\", "/").rstrip("/")
        if root_rel and meta_rel.startswith(root_rel + "/"):
            rel = meta_rel[len(root_rel) + 1 :]
        else:
            rel = meta_rel
        if rel.endswith(".txt"):
            rel = rel[:-4]
        folder = str(folder_path or "").strip().rstrip("/")
        if folder and folder != ".":
            return f"{folder}/{rel}" if rel else folder
        return rel or meta_file_path
    except Exception:
        return meta_file_path

def _load_registry_file_entry(meta_file_path, folder_path, metadata_root):
    """Load one metadata file as name/type/description/functions/path only."""
    source_path = _source_path_from_metadata(
        meta_file_path,
        folder_path,
        metadata_root,
    )

    try:
        ensure_module_path()
        from modules.meta_writer import sanitize_module_metadata

        p = resolve_project_path(meta_file_path)
        txt = p.read_text(encoding="utf-8", errors="replace")
        content = _parse_metadata_file_content(txt)
        if isinstance(content, dict) and content.get("path"):
            source_path = content["path"]

        entry = sanitize_module_metadata(content, path=source_path)
        if not entry.get("path"):
            entry["path"] = source_path
        return _truncate_registry_content(entry)

    except Exception as e:
        return {"path": source_path, "error": str(e)}

def folder_registry_dict(folder_path):
    """
    Build one tracked-folder registry context as a nested dictionary.

    This is one-turn context, not persistent selected state.
    """

    ctx = next(
        (x for x in registry_contexts() if x.get("folder_path") == folder_path),
        None,
    )

    if not ctx:
        return {
            "folder_path": folder_path,
            "error": "tracked folder not found",
        }

    folder_path = ctx.get("folder_path", "")
    metadata_path = ctx.get("metadata_path", "")
    metadata_files = ctx.get("metadata_files", [])

    files = {}
    for meta_file_path in metadata_files:
        file_entry = _load_registry_file_entry(
            meta_file_path,
            folder_path,
            metadata_path,
        )
        if not isinstance(file_entry, dict):
            continue
        key = file_entry.get("path") or meta_file_path
        files[key] = file_entry

    block = {
        "folder_path": folder_path,
        "code_type": ctx.get("code_type", ""),
        "files": files,
    }

    if not files:
        block["note"] = (
            f"No registry metadata found for {folder_path}. "
            f"Run Refresh Registries after adding tracked folders."
        )

    return block

def build_registry_context(groups):
    """Build compact nested registry context for selected tracked folders."""
    registries = {}
    for group in groups or []:
        block = folder_registry_dict(group)
        key = block.get("folder_path") or str(group)
        registries[key] = block
    return {"registries": registries}

def render_module_registry(groups):
    """Render selected tracked folders as one compact module registry JSON block."""
    payload = build_registry_context(groups)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"<module_registry>\n{compact}\n</module_registry>"


def render_registry_context(groups):
    """Backward-compatible alias for render_module_registry."""
    return render_module_registry(groups)
# file tree / injection
def file_tree(max_files=1200):
    root=project_root().resolve(); count=0
    def build(dirp):
        nonlocal count
        children=[]
        try: entries=sorted(dirp.iterdir(),key=lambda p:(not p.is_dir(),p.name.lower()))
        except Exception: entries=[]
        for p in entries:
            if p.is_dir():
                if p.name in EXCLUDE_DIRS: continue
                r=relpath(p)
                if r=='agent_memory/jobs' or r.startswith('agent_memory/jobs/'): continue
                node=build(p)
                if node['children']: children.append(node)
            else:
                if p.suffix.lower() in EXCLUDE_SUFFIXES: continue
                r=relpath(p)
                if r.startswith('agent_memory/jobs/'): continue
                try: size=p.stat().st_size
                except Exception: size=0
                if size>2_000_000: continue
                children.append({'type':'file','name':p.name,'path':r,'size':size}); count+=1
                if count>=max_files: break
        return {'type':'dir','name':dirp.name if dirp!=root else '.','path':relpath(dirp),'children':children}
    return build(root)
def read_context_file(path, index, n=30000):
    """
    Render one file as prompt context.

    Format:
    <file_1 path="...">
    <content>
    ...
    </content>
    <code_table>
    ...
    </code_table>
    </file_1>

    The code_table block is best-effort. If generation fails, it is omitted.
    """

    tag = f"file_{index}"

    try:
        p = resolve_project_path(path)

        if not p.exists() or not p.is_file():
            return (
                f'<{tag} path="{path}">\n'
                f'<content_error>\n'
                f'file not found\n'
                f'</content_error>\n'
                f'</{tag}>'
            )

        txt = p.read_text(encoding="utf-8", errors="replace")

        if len(txt) > n:
            txt = txt[:n] + "\n[TRUNCATED]"

        parts = [
            f'<{tag} path="{path}">',
            '<content>',
            txt,
            '</content>',
        ]

        try:
            code_table = maybe_generate_code_table(path)
            if code_table and str(code_table).strip():
                parts.extend([
                    '<code_table>',
                    str(code_table),
                    '</code_table>',
                ])
        except Exception:
            pass

        parts.append(f'</{tag}>')

        return "\n".join(parts)

    except Exception as e:
        return (
            f'<{tag} path="{path}">\n'
            f'<content_error>\n'
            f'{e}\n'
            f'</content_error>\n'
            f'</{tag}>'
        )
def maybe_generate_code_table(path):
    """
    Best-effort code table generation.

    Return empty string if:
    - file is not code-like,
    - generate_table is unavailable,
    - generation fails.
    """

    suffix = str(path).lower().rsplit(".", 1)[-1] if "." in str(path) else ""

    code_like_suffixes = {
        "py",
        "ts",
        "tsx",
        "js",
        "jsx",
        "html",
        "htm",
        "css",
    }

    if suffix not in code_like_suffixes:
        return ""

    try:
        ensure_module_path()
        from modules.generate_table import generate_table

        table = generate_table([path])

        if not isinstance(table, str):
            return str(table)

        return table

    except Exception:
        return ""
def final_task(prompt, files, groups):
    prompt = str(prompt).strip()
    files = files or []
    groups = groups or []

    parts = []

    parts.append("<user_request>")
    parts.append(prompt)
    parts.append("</user_request>")

    if groups:
        parts.append(render_module_registry(groups))

    if files:
        parts.append("<file_context>")
        for i, path in enumerate(files, start=1):
            parts.append(read_context_file(path, index=i, n=30000))
        parts.append("</file_context>")

    return "\n\n".join(parts)
def terminate_process_group(pid, grace_seconds=2.0):
    """
    Terminate worker process group.

    Worker is launched with start_new_session=True, so pid is also process group id.
    """
    try:
        pid = int(pid)
    except Exception:
        return False, "invalid pid"

    if pid <= 0:
        return False, "invalid pid"

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True, "process already exited"
    except Exception as e:
        return False, f"SIGTERM failed: {e}"

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not alive(pid):
            return True, "terminated"
        time.sleep(0.1)

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, "terminated after SIGTERM"
    except Exception as e:
        return False, f"SIGKILL failed: {e}"

    return True, "killed"
def stop_current_job():
    ensure_storage()

    cur = load_current()
    jid = cur.get("job_id")
    pid = cur.get("pid")

    if not jid:
        return {
            "success": True,
            "stopped": False,
            "reason": "no current job",
        }

    try:
        jid = safe_job_id(jid)
    except Exception as e:
        save_current({})
        return {
            "success": False,
            "stopped": False,
            "reason": f"invalid current job id: {e}",
        }

    ok, msg = terminate_process_group(pid)

    update_status(
        jid,
        status="cancelled",
        success=False,
        ended_at=now(),
        reason=f"cancelled by user: {msg}",
    )

    save_current({})

    return {
        "success": bool(ok),
        "stopped": True,
        "job_id": jid,
        "pid": pid,
        "reason": msg,
    }
def stop_all_jobs():
    ensure_storage()

    stop_result = stop_current_job()
    queued = load_queue()
    save_queue([])

    for jid in queued:
        try:
            update_status(
                safe_job_id(jid),
                status="cancelled",
                success=False,
                ended_at=now(),
                reason="removed from queue by user",
            )
        except Exception:
            pass

    return {
        "success": True,
        "stopped_current": stop_result,
        "cleared_queue": queued,
        "queue_count": len(queued),
    }
def restart_job(data):
    ensure_storage()

    jid = str(data.get("job_id", "")).strip()
    if not jid:
        raise ValueError("job_id required")

    jid = safe_job_id(jid)
    d = job_dir(jid)

    if not d.exists():
        raise FileNotFoundError("job not found")

    config = read_json(d / "config.json", None)
    if not isinstance(config, dict):
        raise RuntimeError("job has no valid config.json")

    # If restarting the currently running job, stop it first.
    cur = load_current()
    if cur.get("job_id") == jid:
        stop_current_job()

    new_jid = create_job(config)
    started = start_next()

    return {
        "success": True,
        "old_job_id": jid,
        "new_job_id": new_jid,
        "started": started,
    }
def restart_current_job():
    ensure_storage()

    cur = load_current()
    jid = cur.get("job_id")

    if not jid:
        return {
            "success": False,
            "reason": "no current job to restart",
        }

    jid = safe_job_id(jid)
    d = job_dir(jid)

    config = read_json(d / "config.json", None)
    if not isinstance(config, dict):
        raise RuntimeError("current job has no valid config.json")

    stop_result = stop_current_job()

    new_jid = create_job(config)
    started = start_next()

    return {
        "success": True,
        "stopped": stop_result,
        "old_job_id": jid,
        "new_job_id": new_jid,
        "started": started,
    }
def normalize_submission(data):
    if not isinstance(data,dict): data={}
    prompt=data.get('prompt') or data.get('task') or ''
    if not str(prompt).strip(): raise ValueError('prompt required')
    files=data.get('selected_files') if isinstance(data.get('selected_files'),list) else []
    groups=data.get('selected_registry_groups') if isinstance(data.get('selected_registry_groups'),list) else []
    ensure_module_path()
    from modules.model_config import get_role_config
    planner=get_role_config('main_planner')
    llm_source=str(data.get('llm_source') or data.get('source') or planner.get('source') or 'relay').strip().lower()
    if llm_source not in ('relay','cursor'): llm_source=planner.get('source') or 'relay'
    model=str(data.get('model') or planner.get('model') or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    effort=str(data.get('effort') or planner.get('effort') or DEFAULT_EFFORT).strip() or DEFAULT_EFFORT
    cursor_params=data.get('cursor_params') if isinstance(data.get('cursor_params'), list) else planner.get('cursor_params')
    try: max_tokens=int(data.get('max_tokens') if data.get('max_tokens') is not None else planner.get('max_tokens') or DEFAULT_MAX_TOKENS)
    except Exception: max_tokens=int(planner.get('max_tokens') or DEFAULT_MAX_TOKENS)
    out={'task':final_task(str(prompt).strip(),files,groups),'original_prompt':str(prompt).strip(),'selected_files':files,'selected_registry_groups':groups,'llm_source':llm_source,'model':model,'effort':effort,'max_tokens':max_tokens,'shell_instruction_prompt':data.get('shell_instruction_prompt') or DEFAULT_SHELL,'max_iterations':data.get('max_iterations'),'max_feedback_loops':data.get('max_feedback_loops'),'max_retries':data.get('max_retries')}
    if isinstance(cursor_params, list) and cursor_params:
        out['cursor_params']=cursor_params
    return out
# jobs
def create_job(config):
    ensure_storage(); jid='job_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]; d=job_dir(jid); d.mkdir(parents=True,exist_ok=False); t=now()
    st={'job_id':jid,'status':'queued','success':None,'created_at':t,'updated_at':t,'started_at':None,'ended_at':None,'pid':None,'reason':'queued','llm_source':config.get('llm_source'),'model':config.get('model'),'effort':config.get('effort'),'max_tokens':config.get('max_tokens'),'task_preview':config.get('original_prompt',config.get('task',''))[:300]}
    write_json(d/'config.json',config); write_json(d/'status.json',st); write_text(d/'stdout.log',''); write_text(d/'stderr.log',''); write_text(d/'task.txt',config.get('task',''))
    q=load_queue(); q.append(jid); save_queue(q); return jid
def refresh_current():
    c=load_current(); jid,pid=c.get('job_id'),c.get('pid')
    if not jid: save_current({}); return None
    try: jid=safe_job_id(jid)
    except Exception: save_current({}); return None
    st=read_json(job_dir(jid)/'status.json',{}) or {}
    if st.get('status') in TERMINAL: save_current({}); return None
    if pid and alive(pid): return c
    res=read_json(job_dir(jid)/'result.json',None)
    if isinstance(res,dict):
        ok=bool(res.get('success')); update_status(jid,status='completed' if ok else 'failed',success=ok,ended_at=st.get('ended_at') or now(),reason=res.get('reason','worker finished'))
    else: update_status(jid,status='failed',success=False,ended_at=now(),reason='worker process ended without result.json')
    save_current({}); return None
def start_next():
    ensure_storage(); cur=refresh_current()
    if cur and cur.get('job_id'): return {'started':False,'reason':'job already running','current':cur}
    q=load_queue()
    if not q: return {'started':False,'reason':'queue empty','current':None}
    jid=q.pop(0); save_queue(q); d=job_dir(jid); w=worker_script()
    if not w.exists(): update_status(jid,status='failed',success=False,ended_at=now(),reason=f'worker not found: {w}'); return {'started':False,'reason':'worker not found'}
    cmd=[sys.executable,'-u',str(w),'--job-dir',str(d.resolve())]
    p=subprocess.Popen(cmd,cwd=str(project_root()),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    cur={'job_id':jid,'pid':p.pid,'started_at':now(),'cmd':cmd}; save_current(cur); update_status(jid,status='running',success=None,pid=p.pid,started_at=cur['started_at'],reason='worker started'); return {'started':True,'current':cur,'reason':'worker started'}
def list_jobs():
    ensure_storage(); out=[]
    for ch in jobs_dir().iterdir():
        if ch.is_dir():
            st=read_json(ch/'status.json',None)
            if isinstance(st,dict): st.setdefault('job_id',ch.name); out.append(st)
    out.sort(key=lambda x:x.get('updated_at') or x.get('created_at') or '',reverse=True); return out
def job_payload(jid):
    d=job_dir(jid)
    if not d.exists(): raise FileNotFoundError('job not found')
    return {'status':read_json(d/'status.json',{}),'config':read_json(d/'config.json',{}),'result':read_json(d/'result.json',None),'effective_config':read_json(d/'effective_config.json',None)}
def job_logs(jid):
    d=job_dir(jid)
    if not d.exists(): raise FileNotFoundError('job not found')
    return {'job_id':safe_job_id(jid),'stdout':tail(d/'stdout.log',50000),'stderr':tail(d/'stderr.log',20000)}
def tick(): refresh_current(); s=start_next(); return {'queue':load_queue(),'current':load_current(),'started':s}
# subagent API (for calling gen2 from another agent)
def subagent_status():
    ensure_storage()
    refresh_current()
    cur = load_current()
    q = load_queue()
    current_job = None
    if cur and cur.get('job_id'):
        try:
            current_job = read_json(job_dir(cur['job_id']) / 'status.json', {})
        except Exception:
            current_job = cur
    return {
        'success': True,
        'service': 'gen2_web_gui',
        'project_root': str(project_root()),
        'queue_length': len(q),
        'queue': q,
        'current': cur,
        'current_job': current_job,
        'worker': str(worker_script()),
    }
def _job_is_terminal(jid):
    st = read_json(job_dir(jid) / 'status.json', {}) or {}
    return st.get('status') in TERMINAL
def _wait_for_job(jid, timeout_seconds=3600, poll_interval_seconds=2):
    jid = safe_job_id(jid)
    deadline = time.time() + max(1, float(timeout_seconds))
    poll = max(0.2, float(poll_interval_seconds))
    last_status = None
    while time.time() < deadline:
        tick()
        st = read_json(job_dir(jid) / 'status.json', {}) or {}
        last_status = st.get('status')
        if last_status in TERMINAL:
            payload = job_payload(jid)
            status = payload.get('status') or {}
            result = payload.get('result')
            terminal_success = bool(status.get('success')) if status.get('success') is not None else bool(result and result.get('success'))
            return {
                'success': terminal_success,
                'timed_out': False,
                'job_id': jid,
                'status': last_status,
                'reason': status.get('reason') or (result.get('reason') if isinstance(result, dict) else ''),
                'job': payload,
            }
        time.sleep(poll)
    st = read_json(job_dir(jid) / 'status.json', {}) or {}
    return {
        'success': False,
        'timed_out': True,
        'job_id': jid,
        'status': st.get('status') or last_status or 'unknown',
        'reason': f'timed out after {timeout_seconds}s',
        'job': job_payload(jid),
    }
def subagent_run(data):
    """
    Run gen2 as a subagent: submit a job and optionally wait for completion.

    Intended for programmatic use from another agent via HTTP or direct import.
    """
    if not isinstance(data, dict):
        data = {}
    wait = data.get('wait', True)
    if isinstance(wait, str):
        wait = wait.strip().lower() not in ('0', 'false', 'no', '')
    timeout_seconds = data.get('timeout_seconds', 3600)
    poll_interval_seconds = data.get('poll_interval_seconds', 2)
    include_logs = bool(data.get('include_logs', False))
    init_if_needed = data.get('init_if_needed', True)
    if isinstance(init_if_needed, str):
        init_if_needed = init_if_needed.strip().lower() not in ('0', 'false', 'no', '')
    if init_if_needed:
        init_agent()
    config = normalize_submission(data)
    jid = create_job(config)
    started = start_next()
    out = {
        'success': True,
        'job_id': jid,
        'started': started,
        'status': 'queued',
        'poll_url': f'/api/job/{jid}',
        'logs_url': f'/api/job/{jid}/logs',
    }
    if not wait:
        tick()
        st = read_json(job_dir(jid) / 'status.json', {}) or {}
        out['status'] = st.get('status') or 'queued'
        return out
    waited = _wait_for_job(jid, timeout_seconds=timeout_seconds, poll_interval_seconds=poll_interval_seconds)
    out.update({
        'success': bool(waited.get('success')),
        'timed_out': bool(waited.get('timed_out')),
        'status': waited.get('status'),
        'reason': waited.get('reason'),
        'result': (waited.get('job') or {}).get('result'),
        'job': waited.get('job'),
    })
    if include_logs:
        out['logs'] = job_logs(jid)
    return out
# actions
def init_agent():
    ensure_module_path()

    from modules.ensure_memory_files import ensure_memory_files

    # Canonical memory initialization.
    ensure_memory_files()

    # Web GUI queue/current files only.
    ensure_storage()

    return {
        "success": True,
        "message": "initialized using ensure_memory_files",
        "project_root": str(project_root()),
        "source_dir": str(source_dir()),
        "memory_dir": str(memory_dir()),
        "jobs_dir": str(jobs_dir()),
    }
def refresh_all():
    ensure_module_path()

    out = {
        "success": True,
        "results": []
    }
    
    try:
        from modules.refresh_registry import refresh_registry

        ok, msg = refresh_registry()
        out["results"].append({
            "name": "refresh_registry",
            "success": bool(ok),
            "message": str(msg),
        })

        if not ok:
            out["success"] = False

    except Exception as e:
        out["success"] = False
        out["results"].append({
            "name": "refresh_registry",
            "success": False,
            "error": str(e),
        })
    
    """
    try:
        try:
            from modules.refresh_modules_registry import refresh_modules_registry
        except Exception:
            from modules.refresh_module_registry import refresh_modules_registry

        res = refresh_modules_registry()
        out["results"].append({
            "name": "refresh_modules_registry",
            "success": True,
            "message": str(res),
        })

    except Exception as e:
        out["success"] = False
        out["results"].append({
            "name": "refresh_modules_registry",
            "success": False,
            "error": str(e),
        })
    """
    return out
def git_action(data):
    action=str(data.get('action','status')); mp={'status':['status','--short'],'diff':['diff'],'log':['log','--oneline','--graph','--decorate','--all','-n','30'],'add_all':['add','.']}
    if action=='commit':
        msg=str(data.get('message','')).strip()
        if not msg: raise ValueError('commit message required')
        args=['commit','-m',msg]
    elif action in mp: args=mp[action]
    else: raise ValueError('unsupported git action')
    p=subprocess.run(['git']+args,cwd=str(project_root()),capture_output=True,text=True,timeout=120)
    return {'success':p.returncode==0,'cmd':'git '+' '.join(args),'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
# discussion mode
def discussion_gui_defaults():
    ensure_module_path()
    from modules.model_config import get_role_config
    cfg = get_role_config('discussion')
    return {
        'source': cfg.get('source'),
        'model': cfg.get('model'),
        'effort': cfg.get('effort'),
        'max_tokens': cfg.get('max_tokens'),
    }
def discussion_bootstrap():
    ensure_module_path()
    from modules.discussion_mode import discussion_context
    return discussion_context(defaults=discussion_gui_defaults())
def discussion_reset():
    ensure_module_path()
    from modules.discussion_mode import reset_session
    return {'success': True, 'session': reset_session(defaults=discussion_gui_defaults())}
def discussion_post_message(data):
    ensure_module_path()
    from modules.discussion_mode import discussion_send_message, load_session
    defaults = discussion_gui_defaults()
    return discussion_send_message(
        message=str(data.get('message', '')),
        selected_indices=data.get('selected_indices'),
        defaults=defaults,
        session=load_session(),
    )
def discussion_update_settings(data):
    ensure_module_path()
    from modules.discussion_mode import discussion_update_settings, load_session
    return discussion_update_settings(
        defaults=discussion_gui_defaults(),
        session=load_session(),
    )
def discussion_save(data):
    ensure_module_path()
    from modules.discussion_mode import discussion_save_files
    return discussion_save_files(
        project=str(data.get('project', '')),
        plan=str(data.get('plan', '')),
    )
def discussion_discard():
    ensure_module_path()
    from modules.discussion_mode import discussion_discard_files
    return discussion_discard_files()
def discussion_resolve(data):
    ensure_module_path()
    from modules.discussion_mode import discussion_resolve_entries
    entries = data.get('resolved_entries') or data.get('entries') or []
    if not isinstance(entries, list):
        raise ValueError('resolved_entries must be a list')
    return discussion_resolve_entries(entries)
def discussion_skip_resolve():
    ensure_module_path()
    from modules.discussion_mode import load_session, save_session
    session = load_session()
    session['phase'] = 'chat'
    save_session(session)
    return {'success': True, 'session': session}
# model config
def model_config_bootstrap():
    ensure_module_path()
    from modules.model_config import (
        load_model_config,
        default_model_config,
        LLM_ROLES,
        ROLE_LABELS,
        LLM_SOURCES,
        PARSE_FALLBACK_KINDS,
        PARSE_FALLBACK_LABELS,
        role_config_summary,
        parse_fallback_summary,
    )
    return {
        'success': True,
        'config': load_model_config(),
        'defaults': default_model_config(),
        'summary': role_config_summary(),
        'parse_fallback_summary': parse_fallback_summary(),
        'roles': [{'id': role, 'label': ROLE_LABELS.get(role, role)} for role in LLM_ROLES],
        'parse_fallback_kinds': [{'id': kind, 'label': PARSE_FALLBACK_LABELS.get(kind, kind)} for kind in PARSE_FALLBACK_KINDS],
        'sources': list(LLM_SOURCES),
    }
def model_config_save(data):
    ensure_module_path()
    from modules.model_config import save_model_config
    config = data.get('config') if isinstance(data, dict) else {}
    if not config:
        config = data
    saved = save_model_config(config)
    return {'success': True, 'config': saved}
def model_config_list_models(source):
    ensure_module_path()
    from modules.list_llm_models import list_models_for_source
    return list_models_for_source(source)
# http
def jresp(h,obj,status=200):
    b=json.dumps(obj,ensure_ascii=False).encode(); h.send_response(status); h.send_header('Content-Type','application/json; charset=utf-8'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def hresp(h,text,status=200):
    b=text.encode(); h.send_response(status); h.send_header('Content-Type','text/html; charset=utf-8'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def reqjson(h):
    n=int(h.headers.get('Content-Length','0') or 0); return json.loads(h.rfile.read(n).decode()) if n>0 else {}
def load_html():
    p=source_dir()/'gen2_gui.html'
    if p.exists(): return p.read_text(encoding='utf-8')
    return '<html><body><h1>Missing gen2_gui.html</h1></body></html>'
HTML = load_html()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=unquote(urlparse(self.path).path)
        try:
            if path=='/': return hresp(self,HTML)
            if path=='/api/jobs': return jresp(self,list_jobs())
            if path=='/api/file_tree': return jresp(self,file_tree())
            if path=='/api/tracked_folders': return jresp(self,tracked_folders())
            if path=='/api/registry_contexts': return jresp(self,registry_contexts())
            if path=='/api/subagent/status': return jresp(self,subagent_status())
            if path=='/api/discussion/context': return jresp(self,discussion_bootstrap())
            if path=='/api/model_config': return jresp(self,model_config_bootstrap())
            q=urlparse(self.path).query
            if path=='/api/model_config/models':
                from urllib.parse import parse_qs
                params=parse_qs(q)
                source=(params.get('source') or ['relay'])[0]
                return jresp(self,model_config_list_models(source))
            parts=[p for p in path.split('/') if p]
            if len(parts)==3 and parts[:2]==['api','job']: return jresp(self,job_payload(parts[2]))
            if len(parts)==4 and parts[:2]==['api','job'] and parts[3]=='logs': return jresp(self,job_logs(parts[2]))
            return jresp(self,{'error':'not found'},404)
        except Exception as e: return jresp(self,{'error':str(e)},500)
    def do_POST(self):
        path=unquote(urlparse(self.path).path)
        try:
            data=reqjson(self)
            if path=='/api/submit':
                jid=create_job(normalize_submission(data)); started=start_next(); return jresp(self,{'success':True,'job_id':jid,'started':started})
            if path=='/api/tick': return jresp(self,tick())
            if path=='/api/init': return jresp(self,init_agent())
            if path=='/api/refresh_all_registries': return jresp(self,refresh_all())
            if path=='/api/tracked_folders/add': return jresp(self,add_tracked(data))
            if path=='/api/tracked_folders/remove': return jresp(self,remove_tracked(data))
            if path == "/api/stop_current_job":
                return jresp(self, stop_current_job())
            
            if path == "/api/restart_current_job":
                return jresp(self, restart_current_job())
            
            if path == "/api/restart_job":
                return jresp(self, restart_job(data))
            
            if path == "/api/stop_all_jobs":
                return jresp(self, stop_all_jobs())
            if path == "/api/subagent/run":
                return jresp(self, subagent_run(data))
            if path=='/api/discussion/reset':
                return jresp(self, discussion_reset())
            if path=='/api/discussion/message':
                return jresp(self, discussion_post_message(data))
            if path=='/api/discussion/settings':
                return jresp(self, discussion_update_settings(data))
            if path=='/api/discussion/save':
                return jresp(self, discussion_save(data))
            if path=='/api/discussion/discard':
                return jresp(self, discussion_discard())
            if path=='/api/discussion/resolve':
                return jresp(self, discussion_resolve(data))
            if path=='/api/discussion/skip_resolve':
                return jresp(self, discussion_skip_resolve())
            if path=='/api/model_config/save':
                return jresp(self, model_config_save(data))
            if path=='/api/git': return jresp(self,git_action(data))
            return jresp(self,{'error':'not found'},404)
        except Exception as e: return jresp(self,{'success':False,'error':str(e)},500)
    def log_message(self,fmt,*args): sys.stderr.write('[%s] %s\n'%(self.log_date_time_string(),fmt%args))
def run_server(host='127.0.0.1',port=7860):
    os.chdir(project_root()); ensure_storage(); srv=ThreadingHTTPServer((host,port),Handler)
    print(f'Gen2 web GUI serving at http://{host}:{port}/'); print(f'Project root: {project_root()}'); print(f'Source dir: {source_dir()}'); print(f'Worker: {worker_script()}'); print(f'JupyterLab proxy: <base>/proxy/{port}/')
    try: srv.serve_forever()
    except KeyboardInterrupt: print('\nStopping Gen2 web GUI.')
    finally: srv.server_close()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=7860); a=ap.parse_args(); run_server(a.host,a.port)
if __name__=='__main__': main()
