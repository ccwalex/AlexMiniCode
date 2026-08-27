import sys
sys.path.insert(0, "code")
sys.path.insert(0, "code/modules")

from modules.run_task_v2 import run_task_v2

result = run_task_v2(
    "Create code/hello_gen2.py that prints 'hello from gen2', then run it.",
    model="nova",
    effort="l",
    max_tokens=4096,
    shell_instruction_prompt=(
        "Allow creating files under code/. "
        "Allow running python scripts under code/. "
        "Do not allow deleting files or modifying files through shell redirection."
    ),
    max_iterations=5,
    max_feedback_loops=3,
    max_retries=2,
)

print(result["success"], result["status"], result["reason"])