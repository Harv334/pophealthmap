"""The Worker offers the tools at all five levels the map holds.

Ask used to read wards and LAs only. The tools now take ward, la, msoa, lsoa
and oa, with within_la and within_ward to narrow a lookup or ranking (output
areas need one of them), and the system prompt says what each level is. This
checks the tool schemas sent upstream, that a conversation calling a tool at
a small level with within_ward is accepted, and that the prompt names the
levels. Same harness as test_worker_cap (Node, nothing billed).

    python3 tests/pipeline/test_worker_levels.py
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from worker_harness import run, Checks  # noqa: E402

r = run(r"""
const Q = { role: "user", content: "which LSOAs in Church End ward are most deprived?" };
const use = { type: "tool_use", id: "t1", name: "rank_areas",
  input: { indicator: "imd_score", direction: "highest", n: 5, level: "lsoa", within_ward: "Church End" } };
const loop = [Q, { role: "assistant", content: [use] },
  { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "{}" }] }];
upstream.bodies.length = 0;
const res = await send(loop);
const sent = upstream.bodies[0];
const tools = {};
for (const t of sent.tools) {
  const p = t.input_schema.properties;
  tools[t.name] = { levels: p.level ? p.level.enum : null, within_la: !!p.within_la, within_ward: !!p.within_ward };
}
return { status: res.status, tools, prompt: sent.system[0].text };
""")

check = Checks()
check("a small-area tool call with within_ward is accepted", r["status"] == 200, str(r["status"]))
for name in ("get_area", "compare_areas", "rank_areas"):
    t = r["tools"][name]
    check(f"{name} takes all five levels", t["levels"] == ["ward", "la", "msoa", "lsoa", "oa"], json.dumps(t))
    check(f"{name} takes within_la and within_ward", t["within_la"] and t["within_ward"], json.dumps(t))
p = r["prompt"]
check("the prompt says what each level is",
      all(s in p for s in ("MSOA, about 8,000", "LSOA, about 1,500", "output area (oa), about 300 people")), p[:200])
check("the prompt says output areas need an LA or ward", "within_la or within_ward" in p)
check("the prompt asks for the level of each figure", "say which level a figure is for" in p)
check.done()
