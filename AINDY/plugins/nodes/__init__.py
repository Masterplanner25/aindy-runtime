# Plugin nodes directory.
# Drop .py files here and register them via POST /platform/nodes/register
# with type="plugin" and handler="module_name:function_name".
# These modules are imported in-process and are therefore trusted code, not a
# sandboxed plugin model.
#
# Each function must implement the node contract:
#   def my_node(state: dict, context: dict) -> dict:
#       # ... business logic ...
#       return {
#           "status": "SUCCESS",      # SUCCESS | RETRY | FAILURE | WAIT
#           "output_patch": {...},    # state updates
#       }
