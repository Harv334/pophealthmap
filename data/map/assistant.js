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
var ASSISTANT_ENDPOINT = "https://pophealthmapai.sevilleharvey.workers.dev";

(function () {
  "use strict";

  var MAX_TOOL_ROUNDS = 5; // model -> tools -> model, bounded so it cannot loop

  // Shown to the user, and enforced by the Worker. If you change DAILY_CAP in
  // worker/src/index.js, change this too: the panel promising one number while
  // the server enforces another is worse than not mentioning it at all.
  var DAILY_CAP = 40;

  // ---- data access -------------------------------------------------------
  // Read straight from the globals the map already populated.

  function wards() {
    return (typeof WARD_DATA === "object" && WARD_DATA) ? WARD_DATA : {};
  }

  /**
   * Which indicators may be added up across wards.
   *
   * This is an allow-list, not a pattern, and it has to stay that way. The
   * previous version summed anything whose name did not contain _pct, _score
   * or "rate", which quietly swept in every indicator that happens to be
   * named for its subject rather than its unit: imd_decile_mean,
   * ft_smoking_prevalence_adults, ft_life_expectancy_male, the three _qof
   * prevalences, ft_fuel_poverty_lihc and the rest of the Fingertips set.
   * A twenty-ward borough came out with a deprivation decile of 160 and a
   * life expectancy of sixteen hundred years.
   *
   * Adding a genuinely additive indicator means adding it here. Anything not
   * listed is treated as a rate and population-weighted, which is the safe
   * default: a wrongly averaged count looks implausible and gets caught,
   * a wrongly summed rate looks like a number and does not.
   */
  var ADDITIVE = { census_population: 1, crime_total: 1 };
  function isAdditive(key) {
    return ADDITIVE[key] === 1 || /_count$/.test(key);
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
        if (isAdditive(key)) {
          b.indicators[key] = (b.indicators[key] || 0) + v;
        } else {
          var weight = pop > 0 ? pop : 1;
          b._num[key] = (b._num[key] || 0) + v * weight;
          b._den[key] = (b._den[key] || 0) + weight;
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

  /**
   * Fallback metadata for the Fingertips set, which OV_META does not carry.
   *
   * Descriptions match FINGERTIPS_INDICATORS in fetch_all_data.py. Units are
   * given only where the indicator's definition fixes them; a_e_attendance is
   * left without one rather than guessed at.
   */
  var FT_META = {
    ft_life_expectancy_male: ["Life expectancy at birth (male)", "years"],
    ft_life_expectancy_female: ["Life expectancy at birth (female)", "years"],
    ft_healthy_life_expectancy_male: ["Healthy life expectancy at birth (male)", "years"],
    ft_healthy_life_expectancy_female: ["Healthy life expectancy at birth (female)", "years"],
    ft_smoking_prevalence_adults: ["Smoking prevalence in adults (18+), current smokers", "%"],
    ft_obesity_adults: ["Overweight including obesity prevalence in adults", "%"],
    ft_obesity_year6: ["Year 6 obesity, including severe obesity", "%"],
    ft_physical_activity_adults: ["Physically active adults", "%"],
    ft_hypertension_qof: ["Hypertension: QOF prevalence", "%"],
    ft_depression_qof: ["Depression: QOF prevalence", "%"],
    ft_mental_health_qof: ["Mental health: QOF prevalence", "%"],
    ft_suicide_rate: ["Suicide rate", "per 100,000"],
    ft_child_poverty_low_income: ["Children in relative low income families (under 16)", "%"],
    ft_self_harm_admissions_10_24: ["Hospital admissions for self-harm (10 to 24)", "per 100,000"],
    ft_a_e_attendance_under_5: ["A&E attendances, ages 0 to 4", "per 1,000"],
    ft_mmr_2_doses_age5: ["Vaccination coverage: MMR two doses at 5 years", "%"],
    ft_flu_vaccination_65plus: ["Vaccination coverage: flu (65 and over)", "%"],
    ft_cervical_screening_25_49: ["Cancer screening coverage: cervical (25 to 49)", "%"],
  };

  /**
   * Is this indicator published below borough level?
   *
   * The Fingertips pull in fetch_all_data.py uses area type 502, upper-tier
   * local authorities, and the resulting value is written onto every ward in
   * the borough. Checked against the data: all fifteen ft_ indicators are
   * identical for every ward within a borough, while the census ones vary
   * ward by ward in all 33.
   *
   * That makes a ward ranking on an ft_ indicator meaningless, and worse than
   * meaningless if it is stated as a ward finding. The tools carry the caveat
   * so the model cannot present borough data as ward data.
   *
   * Since that was written a second Fingertips set arrived and the test had to
   * narrow. The Local Health series is fetched at MSOA (area type 3) and
   * carried down to wards through the LSOA bridge, and it is keyed by the
   * numeric indicator id: ft_93283, ft_93280 and 31 others. Those genuinely
   * differ ward by ward and ranking them is a real ward ranking. The
   * upper-tier local authority series is keyed by name, ft_life_expectancy_male
   * and the rest, and that is the one that repeats across a borough.
   *
   * Testing /^ft_/ alone put the caveat on both, telling the model not to rank
   * wards by the 33 indicators that are the most ward-specific health data on
   * the map.
   */
  function isBoroughLevel(key) {
    return /^ft_/.test(key) && !/^ft_\d+$/.test(key);
  }

  var BOROUGH_CAVEAT = "This indicator is published at borough level and " +
    "applied to every ward in the borough, so it is the same figure for all " +
    "wards there. Report it as a borough figure, and do not describe it as a " +
    "difference between wards.";

  /**
   * Indicator metadata, from the same table the map's own legends read.
   *
   * OV_META does not have a `label` field. It has `desc`, `u` (unit), `src`
   * (source) and `yr` (year), so the old lookup missed on all 67 indicators
   * and every one of them reached the model as a bare key. That is worse than
   * cosmetic: the model chooses which indicator answers a question from this
   * list, and it was choosing between things like "barriers_score" and
   * "census_bad_health_pct" with nothing to go on but the key, and no unit to
   * tell it whether an answer was a count, a percentage or a score.
   */
  /**
   * The deprivation family, where the scale runs both ways.
   *
   * Scores go up with deprivation. Deciles go down with it: decile 1 is the
   * most deprived tenth, decile 10 the least. Nothing in the data says so.
   * OV_META spells the direction out for imd_score alone, the seven domain
   * scores describe their subject without their direction, and the three
   * decile fields have no entry at all.
   *
   * That is the whole of the trap the assistant fell into on its first live
   * question: asked for the most deprived boroughs it returned the least, and
   * explained that a higher score meant less deprivation.
   */
  var DECILE_SCALE = "Deciles run 1 (the most deprived tenth nationally) to 10 " +
    "(the least). This is an average of them, not a decile itself: a value of 3 " +
    "means the typical LSOA here sits in the third decile, among roughly the 30 " +
    "per cent most deprived. Only a value of 1 would mean the most deprived tenth.";

  var IMD_META = {
    imd_decile_mean: "Mean IMD decile of the LSOAs in this area, population weighted. " + DECILE_SCALE,
    imd_best_decile: "The least deprived IMD decile present in this area. Deciles run 1 (the most deprived tenth nationally) to 10 (the least).",
    imd_worst_decile: "The most deprived IMD decile present in this area. Deciles run 1 (the most deprived tenth nationally) to 10 (the least).",
  };

  var DIRECTION = {
    imd_score: "more deprived",
    barriers_score: "more deprived",
    crime_score: "more deprived",
    education_score: "more deprived",
    employment_score: "more deprived",
    environment_score: "more deprived",
    health_score: "more deprived",
    income_score: "more deprived",
    imd_decile_mean: "less deprived",
    imd_best_decile: "less deprived",
    imd_worst_decile: "less deprived",
  };

  function metaFor(key) {
    var m = (typeof OV_META === "object" && OV_META && OV_META[key]) ? OV_META[key] : null;
    if (m) return m;
    var ft = FT_META[key];
    if (ft) return { desc: ft[0], u: ft[1] || undefined, src: "OHID Fingertips", g: "Borough" };
    var imd = IMD_META[key];
    return imd ? { desc: imd, u: "decile", src: "MHCLG IMD 2025" } : null;
  }

  function labelFor(key) {
    var m = metaFor(key);
    return (m && m.desc) ? m.desc : key;
  }

  /**
   * Which end of the scale is which.
   *
   * Live, the model was asked for the three most deprived boroughs and returned
   * the three least deprived, explaining that a higher IMD score means less
   * deprivation. It is the other way round. The direction was already in the
   * result, but buried in a prose label ("Composite Index of Multiple
   * Deprivation. Higher = more deprived.") where it read as description rather
   * than instruction, and deciles running the opposite way makes the mistake an
   * easy one to fall into.
   *
   * Pulled out of that prose into a field of its own, so the direction arrives
   * as a fact about the data rather than a sentence to be skimmed. Parsed
   * rather than hand-listed, so it stays true to whatever the map's own legend
   * says: the two cannot drift apart.
   */
  function higherMeans(key) {
    if (DIRECTION[key]) return DIRECTION[key];
    var m = metaFor(key);
    if (!m || !m.desc) return undefined;
    var hit = /higher\s*=\s*([^.;]+)/i.exec(m.desc);
    return hit ? hit[1].trim().toLowerCase() : undefined;
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
          var m = metaFor(k);
          return {
            key: k,
            description: m && m.desc ? m.desc : k,
            unit: m && m.u ? m.u : undefined,
            source: m && m.src ? m.src : undefined,
            year: m && m.yr ? m.yr : undefined,
            aggregation: isAdditive(k) ? "summed across wards" : "population-weighted mean",
            published_at: isBoroughLevel(k) ? "borough" : "ward",
            higher_means: higherMeans(k),
          };
        }),
        note: "Deprivation deciles run 1 (most deprived) to 10 (least deprived). " +
          "Indicators marked published_at 'borough' are the same value for every " +
          "ward in a borough; do not rank wards by them.",
      };
    },

    get_area: function (input) {
      var hit = findArea(input.name, input.level);
      if (!hit) return { error: "No " + input.level + " found matching '" + input.name + "'." };
      if (hit.ambiguous) return { error: "Several areas match; be more specific.", candidates: hit.ambiguous };
      var boroughLevel = Object.keys(hit.area.indicators || {}).filter(isBoroughLevel);
      return {
        name: hit.area.name, level: input.level, borough: hit.area.lad || undefined,
        indicators: hit.area.indicators,
        // A ward profile mixes ward figures with borough ones. Name which are
        // which, so a borough figure is not reported as being about the ward.
        borough_level_indicators: (input.level === "ward" && boroughLevel.length)
          ? boroughLevel : undefined,
        caveat: (input.level === "ward" && boroughLevel.length) ? BOROUGH_CAVEAT : undefined,
      };
    },

    compare_areas: function (input) {
      var names = input.names || [], rows = [], missing = [];
      names.slice(0, 6).forEach(function (n) {
        var hit = findArea(n, input.level);
        if (!hit || hit.ambiguous) { missing.push(n); return; }
        var ind = hit.area.indicators, keep = {};
        // imd_decile_mean, not imd_decile. The ward figure is a population
        // weighted mean of LSOA deciles, so it is not itself a decile and is
        // not published under that name. The old key matched nothing and was
        // dropped in silence, so the default comparison came back without any
        // deprivation figure at all.
        var wanted = (input.indicators && input.indicators.length)
          ? input.indicators
          : ["imd_score", "imd_decile_mean", "census_population", "census_over65_pct",
             "census_non_white_pct", "census_no_car_pct"];
        wanted.forEach(function (k) { if (ind[k] !== undefined) keep[k] = ind[k]; });
        rows.push({ name: hit.area.name, indicators: keep });
      });
      if (!rows.length) return { error: "None of those areas were found.", not_found: missing };
      var shown = {};
      rows.forEach(function (r) { Object.keys(r.indicators).forEach(function (k) { shown[k] = 1; }); });
      var boroughLevel = Object.keys(shown).filter(isBoroughLevel);
      return {
        level: input.level, areas: rows, not_found: missing.length ? missing : undefined,
        // Comparing two wards in the same borough on a borough-level indicator
        // will show them as identical, which is an artefact of the geography
        // rather than a finding about the wards.
        borough_level_indicators: (input.level === "ward" && boroughLevel.length)
          ? boroughLevel : undefined,
        caveat: (input.level === "ward" && boroughLevel.length) ? BOROUGH_CAVEAT : undefined,
      };
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
      var meta = metaFor(key);
      var hm = higherMeans(key);
      return {
        indicator: key, label: labelFor(key), direction: input.direction,
        // Spelled out so the answer cannot invert the scale: this says what a
        // high value means, and what the rows you are looking at therefore are.
        higher_means: hm,
        results_are: hm ? "the " + input.direction + " values; higher = " + hm : undefined,
        unit: meta && meta.u ? meta.u : undefined,
        source: meta && meta.src ? meta.src : undefined,
        level: input.level, of_total: rows.length, results: rows.slice(0, n),
        // A ward ranking on a borough-level indicator is really a borough
        // ranking with the wards of each borough tied. Say so here rather
        // than let the ordering imply a distinction that is not in the data.
        caveat: (input.level === "ward" && isBoroughLevel(key)) ? BOROUGH_CAVEAT : undefined,
      };
    },
  };

  /**
   * The tool layer, exposed for testing.
   *
   * These four functions are what actually produce every figure the assistant
   * states, so they are the part worth testing, and they can be tested without
   * an API key or a deployed Worker: they are pure functions over the JSON the
   * page has already loaded. Nothing here mutates map state.
   */
  window.PH_ASSISTANT = {
    runTool: function (n, i) { return runTool(n, i); },
    areasFor: areasFor,
    findArea: findArea,
    isAdditive: isAdditive,
    // The map's query builder reads indicator metadata through these rather
    // than repeating the OV_META / FT_META / IMD_META fallback chain. Two
    // copies of that chain would drift, and the one that drifted would be the
    // one nobody was testing.
    metaFor: metaFor,
    labelFor: labelFor,
    higherMeans: higherMeans,
    isBoroughLevel: isBoroughLevel,
    get configured() { return !!ASSISTANT_ENDPOINT; },
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
  // Last count the Worker reported. Null until a question has been asked,
  // because the browser has no way to know the count on its own.
  var lastLimit = null;
  var limitEl = null;

  function renderLimit() {
    if (!limitEl) return;
    if (lastLimit && typeof lastLimit.remaining === "number") {
      limitEl.textContent = lastLimit.remaining + " of " + (lastLimit.cap || DAILY_CAP)
        + " questions left today. This is a free service with running costs, so it "
        + "is capped per user. The map itself is unlimited.";
    } else {
      limitEl.textContent = "This is a free service with running costs, so questions "
        + "are limited to " + DAILY_CAP + " a day per user. The map itself is unlimited.";
    }
  }

  /**
   * Keep the conversation under the Worker's message limit.
   *
   * The Worker refuses anything over 40 messages. A question costs about four
   * messages once tool calls are counted, so without this the panel dies for
   * good after roughly ten questions, and the only way out is a page reload.
   *
   * Whole exchanges are dropped, never individual messages: a tool_result must
   * keep the assistant tool_use that produced it, and cutting between them
   * leaves the API with an orphan block it will reject. A typed question is a
   * user message whose content is a plain string, which is exactly where an
   * exchange starts. Trimming happens only between questions, so the pairs
   * built up during a tool loop are never split.
   */
  var MAX_HISTORY = 24; // plus at most 10 more from one question's tool rounds

  function trimHistory() {
    if (history.length <= MAX_HISTORY) return;
    var starts = [];
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      if (m.role === "user" && typeof m.content === "string") starts.push(i);
    }
    for (var s = 0; s < starts.length; s++) {
      if (history.length - starts[s] <= MAX_HISTORY) {
        history.splice(0, starts[s]);
        return;
      }
    }
    if (starts.length) history.splice(0, starts[starts.length - 1]);
  }

  async function ask(question) {
    history.push({ role: "user", content: question });
    trimHistory();
    for (var round = 0; round < MAX_TOOL_ROUNDS; round++) {
      var res = await fetch(ASSISTANT_ENDPOINT, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data && data.error ? data.error : "Request failed");
      if (data && data._limit) lastLimit = data._limit;

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

  var EXAMPLES = [
    "Compare deprivation in Brent and Bromley",
    "Which 5 wards have the highest over-65 population?",
    "Tell me about Notting Dale",
    "Which borough has the most GP practices?",
    "How deprived is Hackney?",
  ];

  /**
   * Build the chat into a container the caller owns.
   *
   * The assistant used to be a floating button over the map. It now lives
   * behind the Ask mode in the rail, so this mounts rather than positions
   * itself, and mounting twice into the same container is a no-op: switching
   * modes back and forth must not stack a second chat or lose the history.
   */
  function mount(container) {
    if (!container || container.dataset.aiMounted === "1") return;
    container.dataset.aiMounted = "1";

    var log = document.createElement("div");
    log.id = "ai-log";

    var intro = document.createElement("div");
    intro.className = "ai-msg ai-bot";
    intro.textContent = "Ask about any ward or borough on the map. Every figure " +
      "in an answer is read from the data already loaded in your browser, so " +
      "nothing is uploaded and no number is written from memory.";
    log.appendChild(intro);

    // Say the limit up front. It is a shared, funded service, and finding out
    // it is capped by being refused mid-question is a worse way to learn it.
    // The count updates from the Worker after each answer, because the browser
    // has no way to know it: the cap is per IP and enforced server-side.
    var limit = document.createElement("div");
    limit.className = "ai-note";
    log.appendChild(limit);

    limitEl = limit;
    renderLimit();

    // Answers are built from the map's own figures, but the sentence around
    // them is still written by a model, and this data gets used to argue for
    // where services go. Saying so once, plainly, at the top.
    var caution = document.createElement("div");
    caution.className = "ai-caution";
    caution.textContent = "AI generated output should be independently verified "
      + "before making any decisions.";
    log.appendChild(caution);

    var chips = document.createElement("div");
    chips.className = "ai-examples";
    EXAMPLES.forEach(function (ex) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "ai-chip";
      b.textContent = ex;
      b.addEventListener("click", function () { submit(ex); });
      chips.appendChild(b);
    });
    log.appendChild(chips);

    var form = document.createElement("form");
    form.id = "ai-form";
    var input = document.createElement("input");
    input.id = "ai-input";
    input.autocomplete = "off";
    input.placeholder = "Ask a question about the data...";
    var send = document.createElement("button");
    send.type = "submit";
    send.id = "ai-send";
    send.textContent = "Ask";
    form.appendChild(input);
    form.appendChild(send);

    container.appendChild(log);
    container.appendChild(form);

    function add(cls, text, asHtml) {
      var d = document.createElement("div");
      d.className = "ai-msg " + cls;
      if (asHtml) d.innerHTML = text; else d.textContent = text;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    }

    async function submit(q) {
      q = String(q || "").trim();
      if (!q || busy) return;
      input.value = "";
      busy = true;
      send.disabled = true;
      if (chips.parentNode) chips.parentNode.removeChild(chips);
      add("ai-user", q);
      var thinking = add("ai-bot ai-thinking", "Reading the data...");
      try {
        var answer = await ask(q);
        thinking.className = "ai-msg ai-bot";
        thinking.innerHTML = render(answer);
        renderLimit();
      } catch (err) {
        thinking.className = "ai-msg ai-bot ai-error";
        thinking.textContent = (err && err.message) ? err.message : "Something went wrong.";
      } finally {
        busy = false;
        send.disabled = false;
        log.scrollTop = log.scrollHeight;
        input.focus();
      }
    }

    form.addEventListener("submit", function (e) { e.preventDefault(); submit(input.value); });
    input.focus();
  }

  window.PH_ASSISTANT.mount = mount;

  /**
   * Put the assistant beside the map.
   *
   * It briefly lived behind an Ask tab, which was the wrong shape: asking about
   * an area is something you do while looking at it, and a tab meant leaving
   * the map to ask and returning to look. A docked panel keeps both on screen.
   *
   * It attaches to .map-wrap, the map's own positioned ancestor, so it cannot
   * stray over the sidebar, and it mounts the chat only on first open, so the
   * thread survives closing and reopening.
   */
  function initFloating() {
    if (!ASSISTANT_ENDPOINT) return; // not configured: no panel, map unaffected
    var host = document.querySelector(".map-wrap");
    if (!host || document.getElementById("ai-panel")) return;

    var panel = document.createElement("div");
    panel.id = "ai-panel";

    var head = document.createElement("div");
    head.id = "ai-head";
    var heading = document.createElement("span");
    heading.textContent = "Ask about this data";
    var close = document.createElement("button");
    close.id = "ai-close";
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.textContent = "×";
    head.appendChild(heading);
    head.appendChild(close);

    var body = document.createElement("div");
    body.id = "ai-body";
    panel.appendChild(head);
    panel.appendChild(body);

    var toggle = document.createElement("button");
    toggle.id = "ai-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "Ask about this data";

    host.appendChild(panel);
    host.appendChild(toggle);

    function open() {
      panel.classList.add("open");
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "true");
      mount(body);
      var input = document.getElementById("ai-input");
      if (input) input.focus();
    }

    function shut() {
      panel.classList.remove("open");
      toggle.hidden = false;
      toggle.setAttribute("aria-expanded", "false");
      toggle.focus();
    }

    toggle.addEventListener("click", open);
    close.addEventListener("click", shut);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.classList.contains("open")) shut();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFloating);
  } else {
    initFloating();
  }
})();
