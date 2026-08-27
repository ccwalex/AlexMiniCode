import sys
import os
sys.path.insert(0, os.path.abspath('code/modules'))
from build_react_tsx_block_table import build_react_tsx_block_table

source_code = """import React, { useState, useEffect } from 'react';
import { MyHelper } from './helper';

export const MyComponent = ({ prop1 }) => {
  const [state, setState] = useState(0);
  const myVar = 42;

  useEffect(() => {
    console.log(state);
  }, [state]);

  const handleUpdate = () => {
    setState(state + 1);
  };

  return (
    <div>
      {state > 0 ? (
        <span>Positive {state}</span>
      ) : (
        <span>Zero</span>
      )}
      <button onClick={handleUpdate}>Update</button>
      {['a', 'b'].map(item => {
        return <div key={item}>{item}</div>;
      })}
    </div>
  );
};
"""

rows = build_react_tsx_block_table(source_code, path="MyComponent.tsx", max_depth=4, min_chunk_lines=1)

print("PRODUCED ROWS:")
for r in rows:
    print(r)

ids = [r['id'] for r in rows]
assert 'mod_1' in ids, 'mod_1 missing'
assert any('comp_' in idx for idx in ids), 'no component row found'
assert any('handler_' in idx or 'hook_' in idx for idx in ids), 'no handler or hook found'

mod_vars = next(r['vars_defined'] for r in rows if r['id'] == 'mod_1')
print("\nMODULE VARS:", mod_vars)
assert 'React' in mod_vars, 'React not in module vars'
assert 'MyComponent' in mod_vars, 'MyComponent not in module vars'

print("\nValidation Passed!")
