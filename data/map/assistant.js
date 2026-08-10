/**
 * PopHealth Map assistant.
 *
 * The model never receives the dataset. It receives tool definitions, and the
 * four functions below run against the JSON this page has already loaded. So
 * every figure in an answer is the same number the map is drawing, nothing is
 * uploaded, and the model cannot invent a statistic: if a tool returns nothing,
 * there is nothing for it to report.
 *
 * Set ASSISTANT_ENDPOINT to the deployed Worker URL. While it is empty the
 * panel stays hidden, so the map works normally without the AI layer.
 */
var ASSISTANT_ENDPOINT = "";

(function () {
  "use strict";

  var MAX_TOOL_ROUNDS = 5; // model -> tools -> model, bounded so it cannot loop

  // ---- data access -------------------------------------------------------
  // Read straight from the globals the map already populated.

  function wards() {
    return (typeof WARD_DATA === "object" && WARD_DATA) ? WARD_DATA : {};
  }

  function boroughRollup() {
    // Boroughs are not a stored level: aggregate wards up, population-weighted
    // for rates and summed for counts, mirroring how the map builds ward
    // figures from LSOAs.
    var out = {};
    var w = wards();
    Object.keys(w).forEach(function (k) {
      var ward = w[k];
      var lad = ward.lad || "";
      if (!lad) return;
      if (!out[lad]) out[lad] = { name: lad, _wards: 0, indicators: {}, _num: {}, _den: {} };
      var b = out[lad];
      b._wards++;
      var ind = ward.indicators || {};
      var pop = parseFloat(ind.census_population) || 0;
      Object.keys(ind).forEach(function (key) {
        var v = parseFloat(ind[key]);
        if (isNaN(v)) return;
        if (key.endsWith("_pct") || key.endsWith("_score") || key.indexOf("rate") !== -1) {
          var weight = pop > 0 ? pop : 1;
          b._num[key] = (b._num[key] || 0) + v * weight;
          b._den[key] = (b._den[key] || 0) + weight;
        } else {
          b.indicators[key] = (b.indicators[key] || 0) + v;
        }
      });
    });
    Object.keys(out).forEach(function (lad) {
      var b = out[lad];
      Object.keys(b._num).forEach(function (key) {
        if (b._den[key] > 0) b.indicators[key] = Math.round((b._num[key] / b._den[key]) * 100) / 100;
      });
      delete b._num; delete b._den;
    });
    return out;
  }

  function areasFor(level) {
    if (level === "borough") return boroughRollup();
    var w = wards(), out = {};
    Object.keys(w).forEach(function (k) {
      out[k] = { name: w[k].name || k, lad: w[k].lad || "", indicators: w[k].indicators || {} };
    });
    return out;
  }

  function findArea(name, level) {
    var areas = areasFor(level);
    var want = String(name || "").trim().toLowerCase();
    var keys = Object.keys(areas);
    var exact = keys.filter(function (k) { return (areas[k].name || "").toLowerCase() === want; });
    if (exact.length) return { key: exact[0], area: areas[exact[0]] };
    var partial = keys.filter(function (k) {
      return (areas[k].name || "").toLowerCase().indexOf(want) !== -1;
    });
    if (partial.length === 1) return { key: partial[0], area: areas[partial[0]] };
    if (partial.length > 1) {
      return { ambiguous: partial.slice(0, 8).map(function (k) { return areas[k].name; }) };
    }
    return null;
  }

  function labelFor(key) {
    if (typeof OV_META === "object" && OV_META && OV_META[key] && OV_META[key].label) {
      return OV_META[key].label;
    }
    return key;
  }

  // ---- the tools ---------------------------------------------------------

  var TOOLS = {
    list_indicators: function () {
      var seen = {}, w = wards();
      Object.keys(w).forEach(function (k) {
        Object.keys(w[k].indicators || {}).forEach(function (i) { seen[i] = true; });
      });
      return {
        indicators: Object.keys(seen).sort().map(function (k) {
          return { key: k, label: labelFor(k) };
        }),
        note: "Deprivation deciles run 1 (most deprived) to 10 (least deprived).",
      };
    },

    get_area: function (input) {
      var hit = findArea(input.name, input.level);
      if (!hit) return { error: "No " + input.level + " found matching '" + input.name + "'." };
      if (hit.ambiguous) return { error: "Several areas match; be more specific.", candidates: hit.ambiguous };
      return {
        name: hit.area.name, level: input.level, borough: hit.area.lad || undefined,
        indicators: hit.area.indicators,
      };
    },

    compare_areas: function (input) {
      var names = input.names || [], rows = [], missing = [];
      names.slice(0, 6).forEach(function (n) {
        var hit = findArea(n, input.level);
        if (!hit || hit.ambiguous) { missing.push(n); return; }
        var ind = hit.area.indicators, keep = {};
        var wanted = (input.indicators && input.indicators.length)
          ? input.indicators
          : ["imd_score", "imd_decile", "census_population", "census_over65_pct",
             "census_non_white_pct", "census_no_car_pct"];
        wanted.forEach(function (k) { if (ind[k] !== undefined) keep[k] = ind[k]; });
        rows.push({ name: hit.area.name, indicators: keep });
      });
      if (!rows.length) return { error: "None of those areas were found.", not_found: missing };
      return { level: input.level, areas: rows, not_found: missing.length ? missing : undefined };
    },

    rank_areas: function (input) {
      var areas = areasFor(input.level), key = input.indicator;
      var n = Math.max(1, Math.min(parseInt(input.n, 10) || 5, 20));
      var rows = [];
      Object.keys(areas).forEach(function (k) {
        var a = areas[k];
        if (input.within_borough &&
            (a.lad || "").toLowerCase().indexOf(String(input.within_borough).toLowerCase()) === -1) return;
        var v = parseFloat((a.indicators || {})[key]);
        if (!isNaN(v)) rows.push({ name: a.name, borough: a.lad || undefined, value: v });
      });
      if (!rows.length) {
        return { error: "No values for indicator '" + key + "'. Call list_indicators for valid keys." };
      }
      rows.sort(function (x, y) { return input.direction === "lowest" ? x.value - y.value : y.value - x.value; });
      return {
        indicator: key, label: labelFor(key), direction: input.direction,
        level: input.level, of_total: rows.length, results: rows.slice(0, n),
      };
    },
  };

  function runTool(name, input) {
    try {
      if (!TOOLS[name]) return { error: "Unknown tool: " + name };
      return TOOLS[name](input || {});
    } catch (e) {
      return { error: "Tool failed: " + (e && e.message ? e.message : String(e)) };
    }
  }

  // ---- conversation ------------------------------------------------------

  var history = [];
  var busy = false;

  async function ask(question) {
    history.push({ role: "user", content: question });
    for (var round = 0; round < MAX_TOOL_ROUNDS; round++) {
      var res = await fetch(ASSISTANT_ENDPOINT, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data && data.error ? data.error : "Request failed");

      history.push({ role: "assistant", content: data.content });

      if (data.stop_reason !== "tool_use") {
        return (data.content || [])
          .filter(function (b) { return b.type === "text"; })
          .map(function (b) { return b.text; })
          .join("\n").trim() || "No answer returned.";
      }
      // Every tool_use block must get exactly one tool_result, in one message.
      var results = (data.content || [])
        .filter(function (b) { return b.type === "tool_use"; })
        .map(function (b) {
          return {
            type: "tool_result",
            tool_use_id: b.id,
            content: JSON.stringify(runTool(b.name, b.input)),
          };
        });
      history.push({ role: "user", content: results });
    }
    return "I could not finish that in a reasonable number of steps. Try a narrower question.";
  }

  // ---- panel -------------------------------------------------------------

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render(text) {
    // Deliberately minimal: escape everything, then allow bold and line breaks.
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  function init() {
    if (!ASSISTANT_ENDPOINT) return; // not configured: no panel, map unaffected

    var panel = document.createElement("div");
    panel.id = "ai-panel";
    panel.innerHTML =
      '<div id="ai-head"><span>Ask about this data</span><button id="ai-close" title="Close">&times;</button></div>' +
      '<div id="ai-log"><div class="ai-msg ai-bot">Ask me about any ward or borough on the map. ' +
      'For example: <em>compare deprivation in Brent and Bromley</em>, or ' +
      '<em>which 5 wards have the highest over-65 population?</em><br><br>' +
      'Every figure comes from the data on this page.</div></div>' +
      '<form id="ai-form"><input id="ai-input" autocomplete="off" ' +
      'placeholder="Ask a question..."><button type="submit" id="ai-send">Ask</button></form>';
    document.body.appendChild(panel);

    var toggle = document.createElement("button");
    toggle.id = "ai-toggle";
    toggle.textContent = "Ask AI";
    toggle.onclick = function () { panel.classList.toggle("open"); };
    document.body.appendChild(toggle);
    document.getElementById("ai-close").onclick = function () { panel.classList.remove("open"); };

    var log = document.getElementById("ai-log");
    function add(cls, html) {
      var d = document.createElement("div");
      d.className = "ai-msg " + cls;
      d.innerHTML = html;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    }

    document.getElementById("ai-form").onsubmit = async function (e) {
      e.preventDefault();
      var input = document.getElementById("ai-input");
      var q = input.value.trim();
      if (!q || busy) return;
      input.value = "";
      busy = true;
      document.getElementById("ai-send").disabled = true;
      add("ai-user", esc(q));
      var thinking = add("ai-bot ai-thinking", "Working through the data...");
      try {
        var answer = await ask(q);
        thinking.className = "ai-msg ai-bot";
        thinking.innerHTML = render(answer);
      } catch (err) {
        thinking.className = "ai-msg ai-bot ai-error";
        thinking.textContent = (err && err.message) ? err.message : "Something went wrong.";
      } finally {
        busy = false;
        document.getElementById("ai-send").disabled = false;
        log.scrollTop = log.scrollHeight;
      }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
