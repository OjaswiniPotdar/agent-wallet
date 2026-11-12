import json, sys
for p in sys.argv[1:]:
    r = json.load(open(p))
    print("==", p)
    for c in r["tool_calls"]:
        print("  call:", c["tool"], c["args"], "->", c["result"][:90])
    print("  log:", [(e["phase"], e["product_id"], e["quantity"], e["amount"], e["host"], e["state"])
                     for e in r["policy_decisions"]])
    print("  out:", r["raw_agent_output"][:250].replace("\n", " "))
    print("  outcome:", {k: v for k, v in r["outcome"].items() if v})