"""The overlay tick-box picker, and that the hidden select stays authoritative."""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902/index.html"
if "?" not in URL:
    URL += "?t=1"
SETTLE = int(sys.argv[2]) if len(sys.argv) > 2 else 28

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1680,1000")
opts.add_argument("--disable-gpu")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


d = webdriver.Chrome(options=opts)
try:
    d.get(URL)
    time.sleep(SETTLE)
    d.execute_script("""[...document.querySelectorAll('.sb-tab')]
        .find(t => t.dataset.tab === 'overlay').click();""")
    time.sleep(1)

    st = d.execute_script("""
      var sel = document.getElementById('ov');
      return {
        selHidden: sel.hidden === true || getComputedStyle(sel).display === 'none',
        picker: !!document.getElementById('ov-picker'),
        groups: document.querySelectorAll('#ov-groups .ov-grp').length,
        boxes: document.querySelectorAll('#ov-groups input[data-ov]').length,
        selOpts: [...sel.querySelectorAll('option')].filter(o => o.value !== 'none').length,
        cur: document.getElementById('ov-cur-nm').textContent.trim(),
        anyOpen: document.querySelectorAll('#ov-groups .ov-grp-body.open').length,
      };
    """)
    check("the dropdown is gone from view", st["selHidden"])
    check("but the select is still in the DOM as the source of truth",
          d.execute_script("return !!document.getElementById('ov')"))
    check("the picker replaced it", st["picker"])
    check("every category is present", st["groups"] == 14, str(st["groups"]))
    check("every indicator became a tick box, none lost",
          st["boxes"] == st["selOpts"], f"{st['boxes']} boxes vs {st['selOpts']} options")
    check("nothing is selected to start with", st["cur"] == "No overlay", st["cur"])
    check("and the categories start collapsed", st["anyOpen"] == 0, str(st["anyOpen"]))

    # ticking one drives the real select and the map
    d.execute_script("""
      document.querySelector('#ov-groups [data-grp="0"]').click();
    """)
    time.sleep(0.5)
    d.execute_script("""
      document.querySelector('#ov-groups input[data-ov="census_bad_health_pct"]').click();
    """)
    time.sleep(2.5)
    one = d.execute_script("""
      return { selValue: document.getElementById('ov').value,
               curOv: typeof curOv !== 'undefined' ? curOv : null,
               cur: document.getElementById('ov-cur-nm').textContent.trim(),
               ticked: document.querySelectorAll('#ov-groups input[data-ov]:checked').length,
               legend: document.getElementById('map-legend-strip').style.display,
               clearShown: !document.getElementById('ov-clear').hidden };
    """)
    check("ticking a box sets the real select", one["selValue"] == "census_bad_health_pct",
          one["selValue"])
    check("and the map's own overlay variable follows",
          one["curOv"] == "census_bad_health_pct", str(one["curOv"]))
    check("the legend appears, so the map actually redrew",
          one["legend"] != "none", one["legend"])
    check("the picker names what is showing", "Bad/very bad health" in one["cur"], one["cur"])
    check("exactly one box is ticked", one["ticked"] == 1, str(one["ticked"]))
    check("and a clear control appears", one["clearShown"])

    # a second tick replaces the first rather than adding to it
    d.execute_script("""
      document.querySelector('#ov-groups [data-grp="0"]').click();
      document.querySelector('#ov-groups input[data-ov="census_good_health_pct"]').click();
    """)
    time.sleep(2.5)
    two = d.execute_script("""
      return { ticked: [...document.querySelectorAll('#ov-groups input[data-ov]:checked')]
                         .map(b => b.getAttribute('data-ov')),
               selValue: document.getElementById('ov').value };
    """)
    check("ticking a second replaces the first, one overlay at a time",
          two["ticked"] == ["census_good_health_pct"]
          and two["selValue"] == "census_good_health_pct", str(two))

    # the filter searches across every category
    d.execute_script("""
      var s = document.getElementById('ov-search');
      s.value = 'obesity'; s.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(1)
    f = d.execute_script("""
      return { boxes: document.querySelectorAll('#ov-groups input[data-ov]').length,
               names: [...document.querySelectorAll('#ov-groups .ov-opt-nm')]
                        .map(e => e.textContent.toLowerCase()),
               open: document.querySelectorAll('#ov-groups .ov-grp-body.open').length,
               grps: document.querySelectorAll('#ov-groups .ov-grp').length };
    """)
    check("the filter narrows the list", 0 < f["boxes"] < 100, str(f["boxes"]))
    check("every result matches the filter",
          all("obesity" in n for n in f["names"]), str(f["names"][:3]))
    check("and matching categories are opened so you can see them",
          f["open"] == f["grps"], f"{f['open']} of {f['grps']}")

    d.execute_script("""
      var s = document.getElementById('ov-search');
      s.value = 'zzzznothing'; s.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(0.8)
    check("an empty result says so rather than going blank",
          "No indicator matches" in d.execute_script(
              "return document.getElementById('ov-groups').textContent"))
    d.execute_script("""
      var s = document.getElementById('ov-search');
      s.value = ''; s.dispatchEvent(new Event('input', {bubbles:true}));
    """)
    time.sleep(0.8)

    # the clear control
    d.execute_script("document.getElementById('ov-clear').click();")
    time.sleep(2)
    cleared = d.execute_script("""
      return { selValue: document.getElementById('ov').value,
               ticked: document.querySelectorAll('#ov-groups input[data-ov]:checked').length,
               cur: document.getElementById('ov-cur-nm').textContent.trim() };
    """)
    check("clearing sets the select back to none",
          cleared["selValue"] == "none" and cleared["ticked"] == 0
          and cleared["cur"] == "No overlay", str(cleared))

    # anything else that sets the overlay re-syncs the ticks
    d.execute_script("""
      var s = document.getElementById('ov');
      s.value = 'imd_score_ward'; s.dispatchEvent(new Event('change', {bubbles:true}));
    """)
    time.sleep(2.5)
    ext = d.execute_script("""
      return { ticked: [...document.querySelectorAll('#ov-groups input[data-ov]:checked')]
                         .map(b => b.getAttribute('data-ov')),
               cur: document.getElementById('ov-cur-nm').textContent.trim() };
    """)
    check("setting the select from elsewhere ticks the right box",
          ext["ticked"] == ["imd_score_ward"], str(ext))

    # Reset clears it through the same path
    d.execute_script("document.getElementById('tb-reset').click();")
    time.sleep(2.5)
    check("Reset clears the picker too",
          d.execute_script("""
            return document.getElementById('ov').value === 'none'
              && document.querySelectorAll('#ov-groups input[data-ov]:checked').length === 0;
          """))

    sev = [e for e in d.get_log("browser")
           if e["level"] == "SEVERE"
           and "favicon" not in e["message"]
           and not ("fonts.gstatic.com" in e["message"] and "404" in e["message"])]
    check("no severe console errors", not sev, str([e["message"][:180] for e in sev[:3]]))
finally:
    d.quit()

print()
print(("FAILED: " + "; ".join(fails)) if fails else "all checks passed")
sys.exit(1 if fails else 0)
