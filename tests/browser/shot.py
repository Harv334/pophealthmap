import pathlib as _pl
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

OUT = str(_pl.Path(__file__).resolve().parent / "_shots")

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1680,1000")
opts.add_argument("--disable-gpu")
d = webdriver.Chrome(options=opts)
try:
    d.get("http://127.0.0.1:8902/index.html")
    time.sleep(26)

    ward = d.execute_script("""
      var n = Object.values(WARD_DATA)
        .filter(w => w && w.lad === 'Brent' && w.indicators
                     && w.indicators.imd_decile_mean != null).map(w => w.name);
      return n[0];
    """)
    d.execute_script("showWard(arguments[0]);", ward)
    time.sleep(2.5)
    d.save_screenshot(OUT + r"\01-sheet.png")

    # pin a second ward so the comparison table has two columns
    d.execute_script("document.getElementById('ws-pin').click();")
    time.sleep(0.5)
    other = d.execute_script("""
      var n = Object.values(WARD_DATA)
        .filter(w => w && w.lad === 'Brent' && w.indicators
                     && w.indicators.imd_decile_mean != null).map(w => w.name);
      return n[3];
    """)
    d.execute_script("showWard(arguments[0]);", other)
    time.sleep(1.5)
    d.execute_script("document.getElementById('ws-pin').click();")
    time.sleep(1.2)
    d.save_screenshot(OUT + r"\02-pinned.png")

    d.execute_script("[...document.querySelectorAll('.sb-tab')].find(t=>t.dataset.tab==='query').click();")
    time.sleep(1.0)
    d.execute_script("document.getElementById('q-run').click();")
    time.sleep(4.0)
    d.execute_script("document.getElementById('ws-head').click();")  # peek, to show the map
    time.sleep(0.6)
    d.save_screenshot(OUT + r"\03-query.png")
    print("ok", ward, "/", other)
finally:
    d.quit()
