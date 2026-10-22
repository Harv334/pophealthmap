/**
 * PopHealth Map assistant.
 *
 * The model is never sent the whole dataset. It receives tool definitions, and
 * the four functions below run against the JSON this page has already loaded,
 * so only the figures a question looks up are sent with it, and they are the
 * same numbers the map is drawing. The model is told to use only those, but
 * nothing enforces it, so an answer can still carry a derived or mistaken
 * number, and the panel says so.
 *
 * That is not the same as nothing leaving the browser. Each question, the
 * conversation so far and every tool result go to the Worker and on to
 * Anthropic, and the Worker keeps the caller's IP for 48 hours to enforce the
 * daily cap. privacy.html says so, and the panel's copy must not contradict it.
 *
 * Set ASSISTANT_ENDPOINT to the deployed Worker URL. While it is empty the
 * panel stays hidden, so the map works normally without the AI layer.
 */
var ASSISTANT_ENDPOINT = "https://pophealthmapai.sevilleharvey.workers.dev";

/**
 * What the panel says is writing the answers, before the first answer arrives.
 *
 * Keep this in step with MODEL in worker/src/index.js, which is the thing that
 * actually decides. Once a reply comes back the panel takes the model name off
 * the reply itself, so a drift here is corrected by the first question anyone
 * asks; it is only the opening state that depends on this line.
 */
var MODEL_LABEL = "Claude Sonnet 5.5";

(function () {
  "use strict";

  var MAX_TOOL_ROUNDS = 5; // model -> tools -> model, bounded so it cannot loop

  // Shown to the user, and enforced by the Worker. If you change DAILY_CAP in
  // worker/src/index.js, change this too: the panel promising one number while
  // the server enforces another is worse than not mentioning it at all.
  var DAILY_CAP = 10;

  /**
   * Turn a model id into something a reader recognises.
   *
   * "claude-sonnet-5" becomes "Claude Sonnet 5", "claude-haiku-4-5" becomes
   * "Claude Haiku 4.5". Anything that does not look like a Claude id is shown
   * verbatim rather than mangled into a wrong name.
   */
  function prettyModel(id) {
    if (!id) return MODEL_LABEL;
    var m = String(id).match(/^claude-([a-z]+)-(\d+)(?:-(\d+))?$/);
    if (!m) return String(id);
    return "Claude " + m[1].charAt(0).toUpperCase() + m[1].slice(1)
      + " " + (m[3] ? m[2] + "." + m[3] : m[2]);
  }
  // The model's only safeguard is its instruction to use the figures it looks
  // up, so the panel does not promise more than that.
  function modelSentence(label) {
    return label + " writes each answer from the figures on this map, but it can"
      + " still get things wrong, so please check any figure against the map or"
      + " its source before you rely on it.";
  }
  function reportModel(id) {
    var el = document.getElementById("ai-model-note");
    if (el) el.textContent = modelSentence(prettyModel(id));
  }

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
  //
  // The rule itself lives in index.html as laSums, which the map, Compare and
  // Custom area also use, so Ask adds up and averages exactly as they do. The
  // list here is only the fallback for a page that has not defined it, and
  // matches it: crime counts (crime_total and the *_12mo categories) and any
  // *_count are summed.
  var ADDITIVE = { census_population: 1, crime_total: 1, gb_total_uprn: 1 };
  function isAdditive(key) {
    if (typeof laSums === "function") return !!laSums(key);
    return ADDITIVE[key] === 1 || /_count$|_12mo$/.test(key);
  }

  // The LA file (borough_data.json), which index.html loads into a
  // top-level BOROUGH_DATA keyed by LAD code. It is not on window, so it is
  // read by name.
  function laFile() {
    return (typeof BOROUGH_DATA === "object" && BOROUGH_DATA) ? BOROUGH_DATA : {};
  }

  function boroughRollup() {
    // LA figures follow the map's rule (boroughOvValue in index.html): the
    // figure the LA file publishes for the LA when it has one, and otherwise
    // the wards aggregated up, population-weighted for rates and summed for
    // counts. So Ask quotes the same LA figure the map shows, including the
    // Census percentages the data build calculates from summed counts.
    // Areas are keyed by the LA's name, which is how questions name them;
    // the LAD code is kept to find the LA's own record.
    var out = {};
    var w = wards();
    Object.keys(w).forEach(function (k) {
      var ward = w[k];
      var lad = ward.lad || "";
      if (!lad) return;
      if (!out[lad]) out[lad] = { name: lad, _wards: 0, indicators: {}, _num: {}, _den: {} };
      var b = out[lad];
      if (!b.lad_code && ward.lad_code) b.lad_code = ward.lad_code;
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
      // Where the map's own LA function is there, take its figure for every
      // indicator, so Ask quotes exactly the number the map draws.
      if (b.lad_code && typeof boroughOvValue === "function") {
        Object.keys(b.indicators).forEach(function (key) {
          var mv = parseFloat(boroughOvValue(b.lad_code, key));
          if (isNaN(mv)) return;
          b.indicators[key] = isAdditive(key) ? mv : Math.round(mv * 100) / 100;
        });
      }
      var own = b.lad_code && laFile()[b.lad_code];
      var rec = own && (own.indicators || own);
      if (rec) {
        Object.keys(rec).forEach(function (key) {
          var v = parseFloat(rec[key]);
          if (!isNaN(v)) b.indicators[key] = v;
        });
      }
    });
    return out;
  }

  // The local authority level is "la" in the Worker's tool schema. Earlier
  // versions of the Worker sent "borough", so both are accepted and mean the
  // same thing.
  function isLaLevel(level) {
    var l = String(level || "").toLowerCase();
    return l === "la" || l === "borough" || l === "local authority";
  }
  function levelWord(level) { return isLaLevel(level) ? "LA" : "ward"; }

  function areasFor(level) {
    if (isLaLevel(level)) return boroughRollup();
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

  var BOROUGH_CAVEAT = "This indicator is published for the whole local " +
    "authority (LA) and applied to every ward in it, so it is the same figure " +
    "for all wards there. Report it as an LA figure, calling it an LA figure " +
    "rather than a borough figure, and do not describe it as a difference " +
    "between wards. Where the LA is a district inside a county, OHID may " +
    "publish only the county's figure, so say it is the county's.";

  /**
   * Series the map leaves out until a data refresh rebuilds them.
   *
   * indicators.js lists the LA Fingertips series whose stored age is not the
   * headline age (ft_age_mismatch). A file written before that check existed
   * has no ft_age_checked, and its adult smoking figure is the 18 to 64
   * breakdown OHID itself flags or withholds, so that one is left out. Ask
   * leaves out the same ones as the map, so it cannot quote a figure the map
   * does not show.
   */
  function withheldKeys() {
    var s = (typeof PH_IND_STATS === "object" && PH_IND_STATS) ? PH_IND_STATS : {};
    if (s.ft_age_checked) return s.ft_age_mismatch || [];
    return ["ft_smoking_prevalence_adults"];
  }
  function isWithheld(key) { return withheldKeys().indexOf(key) !== -1; }
  function shownOnly(ind) {
    var out = {};
    Object.keys(ind || {}).forEach(function (k) { if (!isWithheld(k)) out[k] = ind[k]; });
    return out;
  }

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
    "(the least). This is an average of them, not a decile itself, so report it " +
    "as an average decile to one decimal place (for example 2.3, where 1 is the " +
    "most deprived tenth) and never turn it into a percentage of England.";

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
    if (ft) return { desc: ft[0], u: ft[1] || undefined, src: "OHID Fingertips", g: "LA" };
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

  /**
   * A short name for an indicator, for the key list.
   *
   * The map's own label (indLabel in index.html) where it has one, which is
   * the few words the picker shows. Otherwise the first sentence of the
   * description, cut to a line.
   */
  function shortLabel(key) {
    if (typeof indLabel === "function") {
      try {
        var l = indLabel(key);
        if (l && l !== key) return l;
      } catch (e) { /* fall through to the description */ }
    }
    var d = labelFor(key);
    d = String(d).split(/\.\s/)[0].replace(/\.$/, "");
    return d.length > 90 ? d.slice(0, 87) + "..." : d;
  }

  /**
   * One figure as the tools return it: the value with what it is, its unit
   * and the period it covers, so the model neither guesses an indicator's
   * meaning from its key (ft_obesity_adults is overweight including
   * obesity, not obesity) nor calls list_indicators to find out, and can
   * tell the reader which year to look for. Fields the metadata does not
   * have are left out rather than guessed.
   */
  function figure(key, v) {
    var m = metaFor(key);
    var f = { v: v, label: shortLabel(key) };
    if (m && m.u) f.unit = m.u;
    if (m && m.yr) f.period = String(m.yr);
    return f;
  }

  function figures(ind, keys) {
    var out = {};
    keys.forEach(function (k) {
      if (ind[k] !== undefined && ind[k] !== null && !isWithheld(k)) out[k] = figure(k, ind[k]);
    });
    return out;
  }

  var TOOLS = {
    /* The key list, kept short. It used to send every indicator's full
       description, source, year and aggregation rule, 41 KB that was then
       resent with every later round trip and question; the model needs only
       enough to pick a key, and get_area and rank_areas carry the detail for
       the keys it picks. */
    list_indicators: function () {
      var seen = {}, w = wards();
      Object.keys(w).forEach(function (k) {
        Object.keys(w[k].indicators || {}).forEach(function (i) { seen[i] = true; });
      });
      return {
        indicators: Object.keys(seen).filter(function (k) { return !isWithheld(k); }).sort().map(function (k) {
          var m = metaFor(k);
          var row = { key: k, label: shortLabel(k) };
          if (m && m.u) row.unit = m.u;
          if (m && m.yr) row.period = m.yr;
          if (isBoroughLevel(k)) row.la_only = true;
          var hm = higherMeans(k);
          if (hm) row.higher_means = hm;
          return row;
        }),
        note: "Deprivation deciles run 1 (most deprived) to 10 (least deprived). " +
          "Indicators marked la_only are published for the whole " +
          "local authority (LA), so they are the same value for every ward in " +
          "it; do not rank wards by them. Call the local authority level 'LA' " +
          "in answers, not 'borough'.",
      };
    },

    get_area: function (input) {
      var hit = findArea(input.name, input.level);
      if (!hit) return { error: "No " + levelWord(input.level) + " found matching '" + input.name + "'." };
      if (hit.ambiguous) return { error: "Several areas match; be more specific.", candidates: hit.ambiguous };
      var shownInd = shownOnly(hit.area.indicators);
      // The indicators asked for, or every one the area has.
      var asked = (input.indicators && input.indicators.length) ? input.indicators : Object.keys(shownInd);
      shownInd = figures(shownInd, asked);
      var boroughLevel = Object.keys(shownInd).filter(isBoroughLevel);
      return {
        name: hit.area.name, level: levelWord(input.level), la: hit.area.lad || undefined,
        indicators: shownInd,
        // A ward profile mixes ward figures with borough ones. Name which are
        // which, so a borough figure is not reported as being about the ward.
        la_level_indicators: (input.level === "ward" && boroughLevel.length)
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
        keep = figures(ind, wanted);
        rows.push({ name: hit.area.name, indicators: keep });
      });
      if (!rows.length) return { error: "None of those areas were found.", not_found: missing };
      var shown = {};
      rows.forEach(function (r) { Object.keys(r.indicators).forEach(function (k) { shown[k] = 1; }); });
      var boroughLevel = Object.keys(shown).filter(isBoroughLevel);
      return {
        level: levelWord(input.level), areas: rows, not_found: missing.length ? missing : undefined,
        // Comparing two wards in the same borough on a borough-level indicator
        // will show them as identical, which is an artefact of the geography
        // rather than a finding about the wards.
        la_level_indicators: (input.level === "ward" && boroughLevel.length)
          ? boroughLevel : undefined,
        caveat: (input.level === "ward" && boroughLevel.length) ? BOROUGH_CAVEAT : undefined,
      };
    },

    rank_areas: function (input) {
      var areas = areasFor(input.level), key = input.indicator;
      if (isWithheld(key)) {
        return { error: "'" + key + "' is left out of this version of the map until the data " +
          "is refreshed, because the published figure is not the headline one. Call " +
          "list_indicators for valid keys." };
      }
      var n = Math.max(1, Math.min(parseInt(input.n, 10) || 5, 20));
      var rows = [];
      Object.keys(areas).forEach(function (k) {
        var a = areas[k];
        var within = input.within_la || input.within_borough;
        if (within &&
            (a.lad || "").toLowerCase().indexOf(String(within).toLowerCase()) === -1) return;
        var v = parseFloat((a.indicators || {})[key]);
        if (!isNaN(v)) rows.push({ name: a.name, la: a.lad || undefined, value: v });
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
        period: meta && meta.yr ? String(meta.yr) : undefined,
        source: meta && meta.src ? meta.src : undefined,
        level: levelWord(input.level), of_total: rows.length, results: rows.slice(0, n),
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
  // Set by initFloating once the panel exists; null where there is no panel.
  var openWith = null;

  window.PH_ASSISTANT = {
    runTool: function (n, i) { return runTool(n, i); },
    // Whether the Ask panel is on this page (configured, and in London).
    get available() { return !!openWith; },
    // Opens the panel with a question about the named place in the box.
    askAbout: function (name) {
      if (!openWith || !name) return false;
      openWith("Tell me about " + name);
      return true;
    },
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
    isWithheld: isWithheld,
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

  /**
   * The remaining count, in the panel header beside its title.
   *
   * It used to be a paragraph of its own at the top of the log, above a second
   * paragraph of caution, so opening the panel showed two notices before the
   * first thing you could actually do. The number is the part worth keeping in
   * view, and it belongs where the panel names itself. The reasoning behind
   * the cap moved into the opening message, said once.
   *
   * Before the first question the browser cannot know the count: the cap is
   * per IP and counted by the Worker, so all it can honestly show is the cap.
   */
  /**
   * How many questions today's allowance has left, counted down as it is used.
   *
   * The cap is enforced by the Worker against an IP, so the browser cannot know
   * the true figure until the server has answered at least once. It used to say
   * "10 a day" until then, which is a fact about the policy rather than a
   * countdown, and asking a question and watching a static number is not a
   * countdown either.
   *
   * So this browser keeps its own tally for the UTC day, and every reply the
   * Worker gives overwrites it with the authoritative number. The tally only
   * moves once a question has been answered: the Worker counts a question when
   * the model has answered it, not when it arrives, so a question that never
   * got through has cost nothing and the panel should not say it has. It can
   * undercount an allowance shared across an office IP, which the tooltip says.
   */
  var USED_KEY = "phAskUsed";
  // Bumped whenever the Worker reports a count, so submit() can tell whether
  // a question was counted by the server or still needs counting here.
  var workerCounts = 0;

  function utcDay() { return new Date().toISOString().slice(0, 10); }

  function readUsed() {
    try {
      var raw = window.localStorage.getItem(USED_KEY);
      if (!raw) return 0;
      var o = JSON.parse(raw);
      return (o && o.day === utcDay()) ? (parseInt(o.used, 10) || 0) : 0;
    } catch (e) { return 0; }
  }

  function writeUsed(n) {
    try {
      window.localStorage.setItem(USED_KEY,
        JSON.stringify({ day: utcDay(), used: Math.max(0, n) }));
    } catch (e) { /* private mode: the count is per-session then */ }
  }

  /* This browser's random id, kept in localStorage and sent with each
     request, so the Worker counts the ten questions per browser rather than
     per network address (a whole NHS trust can share one). It names nothing
     and is not linked to anything else; with storage blocked a new one is
     made for the page, and the Worker counts that per address as before. */
  var _clientId = null;
  function clientId() {
    if (_clientId) return _clientId;
    try { _clientId = localStorage.getItem("ph-ask-client"); } catch (e) { /* blocked */ }
    if (!_clientId || !/^[A-Za-z0-9_-]{16,64}$/.test(_clientId)) {
      var b = new Uint8Array(16);
      if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(b);
      else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
      _clientId = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
      try { localStorage.setItem("ph-ask-client", _clientId); } catch (e) { /* blocked */ }
    }
    return _clientId;
  }

  function capNow() { return (lastLimit && lastLimit.cap) || DAILY_CAP; }

  /* ── Is the message about to be sent free? ───────────────────────────────
     The same rule the Worker applies, kept here so the counter does not tick
     down and then climb back a second later when the reply lands and says the
     message was not chargeable. The Worker is still the authority: whatever it
     reports in _limit overwrites this.

     The assistant may ask one clarifying question per query, and the reply to
     it is free. Answering "Brent" to "which Church End?" should not cost the
     same as a question of its own, which is what made asking expensive enough
     to be worth avoiding. */
  function isToolResultOnly(msg) {
    if (!msg || msg.role !== "user" || !Array.isArray(msg.content)) return false;
    if (!msg.content.length) return false;
    return msg.content.every(function (b) { return b && b.type === "tool_result"; });
  }

  function endsWithClarification(msg) {
    if (!msg || msg.role !== "assistant") return false;
    var blocks = Array.isArray(msg.content) ? msg.content : [];
    if (!blocks.length) return false;
    if (blocks.some(function (b) { return b && b.type === "tool_use"; })) return false;
    var text = blocks
      .filter(function (b) { return b && b.type === "text"; })
      .map(function (b) { return String(b.text || ""); })
      .join(" ")
      .trim();
    return text.slice(-1) === "?";
  }

  function nextMessageIsFree() {
    // Replayed rather than read off the last turn, because the free reply is
    // once per question and not once per message.
    var freeUsed = false;
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      if (!m || m.role !== "user" || isToolResultOnly(m)) continue;
      freeUsed = (!freeUsed && endsWithClarification(history[i - 1]));
    }
    return !freeUsed && endsWithClarification(history[history.length - 1]);
  }

  function renderLimit() {
    if (!limitEl) return;
    var cap = capNow();
    var left = Math.max(0, cap - readUsed());
    limitEl.textContent = left + " of " + cap + " left today";
    limitEl.classList.toggle("spent", left === 0);
    limitEl.title = (left === 0
      ? "You have used today's questions. The map, Query, Compare and Directory have no limit. "
      : "")
      + "You can ask " + cap + " questions a day, and the count starts again at midnight UTC. "
      + "A work network shared by many people has a daily ceiling of its own, "
      + "so it may run out sooner.";
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

  /* By size as well as by count. The Worker refuses a conversation over 64 KB,
     and a few questions that each looked up a profile or two used to pass the
     old 200,000 character limit by the fourth question, after which every
     question failed with "Payload too large" until a reload. Between
     questions the history is kept under HISTORY_BYTES, which leaves room for
     the next question's own lookups: first the figures from earlier answers
     are replaced by a short stub (the answers that quoted them stay), then
     whole old exchanges are dropped. Nothing is changed while the history is
     small, so the conversation the Worker caches is not rewritten. */
  // 30 KB: each figure now carries its label, unit and period, so a full
  // profile is about 13 KB, and two of them for the question being asked
  // still fit under the Worker's 64 KB.
  var HISTORY_BYTES = 30000;
  var STUB = "[figures shown earlier]";
  // One tool result may be at most 16 KB at the Worker; a bigger one is
  // replaced by a note asking the model to narrow its request.
  var RESULT_BYTES = 15000;

  function historySize() {
    try { return JSON.stringify(history).length; } catch (e) { return Infinity; }
  }

  function exchangeStarts() {
    var starts = [];
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      if (m.role === "user" && typeof m.content === "string") starts.push(i);
    }
    return starts;
  }

  function trimHistory() {
    var starts = exchangeStarts();
    if (history.length > MAX_HISTORY) {
      for (var s = 0; s < starts.length; s++) {
        if (history.length - starts[s] <= MAX_HISTORY) {
          history.splice(0, starts[s]);
          break;
        }
      }
      if (history.length > MAX_HISTORY && starts.length) history.splice(0, starts[starts.length - 1]);
      starts = exchangeStarts();
    }
    if (historySize() <= HISTORY_BYTES) return;
    // Stub every earlier exchange's tool results; the question being asked
    // now (the last start) has none yet.
    var current = starts.length ? starts[starts.length - 1] : history.length;
    for (var i = 0; i < current; i++) {
      var m = history[i];
      if (m.role === "user" && Array.isArray(m.content)) {
        m.content = m.content.map(function (b) {
          return b && b.type === "tool_result" ? { type: "tool_result", tool_use_id: b.tool_use_id, content: STUB } : b;
        });
      }
    }
    // Then drop whole exchanges from the front until it fits.
    starts = exchangeStarts();
    while (starts.length > 1 && historySize() > HISTORY_BYTES) {
      history.splice(0, starts[1]);
      starts = exchangeStarts();
    }
  }

  /* What the model said, as it goes back into the history. Text and tool
     calls only: nothing else is sent by the map, and the Worker refuses a
     conversation carrying anything else. Empty text blocks are left out too,
     because the API refuses those. */
  function keepBlocks(content, withTools) {
    return (content || []).filter(function (b) {
      if (!b) return false;
      if (b.type === "text") return !!String(b.text || "").trim();
      return withTools && b.type === "tool_use";
    }).map(function (b) {
      return b.type === "text" ? { type: "text", text: b.text }
        : { type: "tool_use", id: b.id, name: b.name, input: b.input || {} };
    });
  }

  function textOf(blocks) {
    return blocks.filter(function (b) { return b.type === "text"; })
      .map(function (b) { return b.text; }).join("\n").trim();
  }

  // Said when an answer stops at the output limit, or the model declines.
  var CUT_SHORT = "This answer was cut short, so please ask about fewer places at once.";
  var DECLINED = "I cannot answer that one. Please ask about the figures on the map.";
  var TOO_LONG = "That conversation has grown too long for me to follow, so I have "
    + "cleared the earlier questions. Please ask again.";

  async function ask(question) {
    /* The map now defers half the ward indicators to a second fetch, so a
       question asked in the first second of a session could be answered from a
       set that is missing all fifty-one Fingertips series. The tools below
       report whatever they find, so the answer would come back confidently
       short rather than visibly wrong, which is the worse of the two.

       A no-op once the merge has landed, and on a deployment that still serves
       the whole ward file there is nothing here to wait for. */
    if (typeof window.ensureWardDetail === "function") {
      try { await window.ensureWardDetail(); } catch (e) {}
    }
    var asked = { role: "user", content: question };
    history.push(asked);
    trimHistory();
    try {
      return await answerRounds();
    } catch (err) {
      // Take the unanswered question back out, so asking it again sends it
      // once rather than run together with its failed first attempt.
      var at = history.indexOf(asked);
      if (at !== -1) history.length = at;
      // A conversation the Worker would not take will not be taken next time
      // either, so it is started again rather than failing for good.
      if (err && err.reset) history.length = 0;
      throw err;
    }
  }

  // "Failed to fetch" is the browser's wording for any request that got no
  // response at all, which here usually means a network that blocks
  // workers.dev. It tells a reader nothing, so the panel says what happened.
  var UNREACHABLE = "The question service could not be reached from this network. "
    + "The rest of the map still works.";

  // A request with no answer after 30 seconds is given up, rather than
  // leaving "Reading the data..." on screen for as long as the network
  // cares to hang, and the panel says so in words.
  var ASK_TIMEOUT_MS = 30000;
  var TIMED_OUT = "Sorry, that question took too long to answer. "
    + "Please try again, or ask it a different way.";

  async function answerRounds() {
    for (var round = 0; round < MAX_TOOL_ROUNDS; round++) {
      var res;
      var ctl = typeof AbortController === "function" ? new AbortController() : null;
      var timer = ctl ? setTimeout(function () { ctl.abort(); }, ASK_TIMEOUT_MS) : 0;
      try {
        res = await fetch(ASSISTANT_ENDPOINT, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ messages: history, client: clientId() }),
          signal: ctl ? ctl.signal : undefined,
        });
      } catch (e) {
        clearTimeout(timer);
        throw new Error(ctl && ctl.signal.aborted ? TIMED_OUT : UNREACHABLE);
      }
      // Not always JSON: a proxy or Cloudflare itself can answer with a page.
      // The 30 seconds cover the body as well, which is where a slow reply
      // spends most of its time.
      var data = null;
      try { data = await res.json(); } catch (e) { /* reported below */ }
      clearTimeout(timer);
      if (ctl && ctl.signal.aborted && !data) throw new Error(TIMED_OUT);
      // Refused for the day. The Worker says so in words; the badge should
      // agree rather than go on offering questions that will be refused.
      // Only this reader's own limit empties the badge: the site's daily
      // ceiling and the one-minute burst limit are not theirs to count.
      if (res.status === 429 && !(data && (data.scope === "site" || data.scope === "burst"))) {
        writeUsed(capNow());
        renderLimit();
      }
      // A conversation the Worker refused as too long or malformed. Asking
      // again would send the same history and be refused the same way, so
      // the history is cleared (in ask) and the reader told.
      if (res.status === 400 || res.status === 413) {
        var e413 = new Error(TOO_LONG);
        e413.reset = true;
        throw e413;
      }
      // The limits' own words are kept. Any other refusal (403, 500, 502)
      // carries the Worker's internal wording, which means nothing to a
      // reader, so it is replaced with one plain sentence.
      if (!res.ok || !data) {
        throw new Error(res.status === 429 && data && data.error ? data.error
          : "Sorry, the question service is not available right now. Please try again later.");
      }
      // The Worker's count is the real one. Whenever it reports, this browser's
      // running tally is set to match rather than being trusted over it.
      // The reply carries the model that actually served it, so the panel can
      // name it rather than trusting a constant on this side to have been kept
      // in step with the Worker.
      if (data && data.model) reportModel(data.model);

      if (data && data._limit) {
        lastLimit = data._limit;
        if (typeof lastLimit.remaining === "number") {
          writeUsed((lastLimit.cap || DAILY_CAP) - lastLimit.remaining);
          workerCounts++;
          renderLimit();
        }
      }

      var stop = data.stop_reason;

      // Declined. Nothing it wrote is kept, and the history records a plain
      // reply so the next question follows on from a finished turn.
      if (stop === "refusal") {
        history.push({ role: "assistant", content: [{ type: "text", text: DECLINED }] });
        return DECLINED;
      }

      // Stopped at the output limit. A tool call cut off there is never run,
      // and a tool call left without its result makes the API refuse every
      // later question, so only the text is kept, and the reader is told the
      // answer is incomplete rather than shown half a table as if it were all.
      if (stop === "max_tokens") {
        var partial = keepBlocks(data.content, false);
        history.push({ role: "assistant", content: partial.length ? partial
          : [{ type: "text", text: "This answer was cut short." }] });
        var said = textOf(partial);
        return (said ? said + "\n\n" : "") + CUT_SHORT;
      }

      var calls = (data.content || []).some(function (b) { return b && b.type === "tool_use"; });
      if (stop !== "tool_use" || !calls) {
        var blocks = keepBlocks(data.content, false);
        var answer = textOf(blocks);
        history.push({ role: "assistant", content: blocks.length ? blocks
          : [{ type: "text", text: "No answer." }] });
        return answer || "Sorry, no answer came back. Please try again, or ask it a different way.";
      }

      var turn = keepBlocks(data.content, true);
      history.push({ role: "assistant", content: turn });
      // Every tool_use block must get exactly one tool_result, in one message.
      var results = turn
        .filter(function (b) { return b.type === "tool_use"; })
        .map(function (b) {
          var out = JSON.stringify(runTool(b.name, b.input));
          if (out.length > RESULT_BYTES) {
            out = JSON.stringify({ error: "That result is too large to send. Ask for fewer " +
              "indicators or fewer areas at once, for example with compare_areas and a short indicators list." });
          }
          return { type: "tool_result", tool_use_id: b.id, content: out };
        });
      history.push({ role: "user", content: results });
    }
    // Out of rounds with tool results last. A short reply closes the turn, so
    // the next question does not follow a reader's turn with another.
    history.push({ role: "assistant", content: [{ type: "text", text: "I could not finish that answer." }] });
    return "Sorry, that question needed more steps than I can take. Please try a narrower question, such as one about a single ward or LA.";
  }

  // ---- panel -------------------------------------------------------------

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* Deliberately minimal: every piece of text is escaped first, then bold,
     line breaks, "- " lists and simple Markdown tables are allowed. Tables
     because the instructions ask for one when comparing places, and the
     first example most people press is a comparison: shown raw, it came back
     as rows of pipes and dashes. A table is a header row, a separator row of
     dashes, then body rows; anything else is left as text. */
  function inline(s) {
    return esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }
  function tableCells(line) {
    var t = String(line).trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.slice(-1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }
  var TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/;
  var LIST_ITEM = /^\s*[-*]\s+/;

  function render(text) {
    var lines = String(text).split(/\r?\n/);
    var parts = []; // { html, block }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.indexOf("|") !== -1 && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
        var head = tableCells(line);
        var rows = [];
        i += 2;
        while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim()) {
          rows.push(tableCells(lines[i]));
          i++;
        }
        i--;
        parts.push({ block: true, html: '<div class="ai-tbl" tabindex="0" role="group" aria-label="Table"><table><thead><tr>'
          + head.map(function (h) { return '<th scope="col">' + inline(h) + "</th>"; }).join("")
          + "</tr></thead><tbody>"
          + rows.map(function (r) {
            return "<tr>" + head.map(function (_, j) { return "<td>" + inline(r[j] || "") + "</td>"; }).join("") + "</tr>";
          }).join("")
          + "</tbody></table></div>" });
        continue;
      }
      if (LIST_ITEM.test(line)) {
        var items = [];
        while (i < lines.length && LIST_ITEM.test(lines[i])) {
          items.push("<li>" + inline(lines[i].replace(LIST_ITEM, "")) + "</li>");
          i++;
        }
        i--;
        parts.push({ block: true, html: "<ul>" + items.join("") + "</ul>" });
        continue;
      }
      parts.push({ block: false, html: inline(line) });
    }
    // Blank lines next to a table or list would only add a gap.
    parts = parts.filter(function (p, k) {
      if (p.block || p.html) return true;
      var prev = parts[k - 1], next = parts[k + 1];
      return !((prev && prev.block) || (next && next.block));
    });
    var out = "";
    parts.forEach(function (p, k) {
      if (k && !p.block && !parts[k - 1].block) out += "<br>";
      out += p.html;
    });
    return out;
  }
  window.PH_ASSISTANT.render = render;

  // The named places are London's. Elsewhere the examples name none, so a
  // region's Ask panel never suggests a question about places it does not hold.
  var IS_LONDON = !window.PH_REGION || window.PH_REGION.isLondon;
  var EXAMPLES = IS_LONDON ? [
    "Compare deprivation in Brent and Bromley",
    "Which 5 wards have the largest share of people aged 65 and over?",
    "Tell me about Notting Dale",
    // Not "the most GP practices": no tool can count them, so that chip was
    // sure to come back as not available and cost the reader a question.
    "Which LA has the lowest life expectancy for men?",
    "How deprived is Hackney?",
  ] : [
    "Which 5 wards are the most deprived?",
    "Which 5 wards have the largest share of people aged 65 and over?",
    "Which LA has the lowest life expectancy for men?",
    "Which ward has the highest crime rate?",
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
    // A log, so an answer arriving after "Reading the data..." is read out
    // rather than appearing silently below whatever has focus.
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-label", "Conversation");

    // One short opening message: what Ask can do. The header counts the
    // questions left, the note under the box asks for nothing personal, and
    // where a question goes is told in full by the tour and the privacy page.
    var intro = document.createElement("div");
    intro.className = "ai-msg ai-bot ai-intro";
    [
      "Ask questions about the data on this map. You can compare areas, or " +
        "find the places with the highest or lowest figures.",
    ].forEach(function (t) {
      var para = document.createElement("p");
      para.textContent = t;
      intro.appendChild(para);
    });
    log.appendChild(intro);

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
    // The Worker takes a typed question of up to 1,000 characters.
    input.maxLength = 1000;
    input.placeholder = "Ask a question about the data...";
    // A placeholder is not a name: it is gone once anyone types.
    input.setAttribute("aria-label", "Your question about the data");
    input.setAttribute("aria-describedby", "ai-caution");
    var send = document.createElement("button");
    send.type = "submit";
    send.id = "ai-send";
    send.textContent = "Ask";
    form.appendChild(input);
    form.appendChild(send);

    // Answers are built from the map's own figures, but the sentence around
    // them is written by a model, and this data gets used to argue for where
    // services go. The caution sits under the input rather than at the top of
    // the log: at the top it was read once, before there was anything to
    // verify, and then scrolled away above every answer it applied to. At the
    // bottom it stays beside them.
    //
    // The caution sits beside the box it is about, and the input is
    // described by this line so a screen reader hears it too.
    // The privacy link opens a new tab so following it does not lose the
    // conversation.
    var caution = document.createElement("div");
    caution.className = "ai-caution";
    caution.id = "ai-caution";
    var warn = document.createElement("strong");
    warn.textContent = "Please do not enter patient or personal information.";
    var privacy = document.createElement("a");
    privacy.href = "privacy.html#ask";
    privacy.target = "_blank";
    privacy.rel = "noopener";
    privacy.textContent = "About and privacy";
    // One line under the box: who writes the answers and how far to trust
    // them, then the personal data warning, then the privacy link.
    // The model is named on the page rather than left to be guessed: read
    // from the Worker's reply when it says so (reportModel), falling back to
    // MODEL_LABEL, which has to be kept in step with MODEL in
    // worker/src/index.js by hand.
    var modelNote = document.createElement("span");
    modelNote.className = "ai-model";
    modelNote.id = "ai-model-note";
    modelNote.textContent = modelSentence(MODEL_LABEL);
    caution.appendChild(modelNote);
    caution.appendChild(document.createTextNode(" "));
    caution.appendChild(warn);
    caution.appendChild(document.createTextNode(" "));
    caution.appendChild(privacy);

    container.appendChild(log);
    container.appendChild(form);
    container.appendChild(caution);

    function add(cls, text, asHtml) {
      var d = document.createElement("div");
      d.className = "ai-msg " + cls;
      if (asHtml) d.innerHTML = text; else d.textContent = text;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      return d;
    }

    async function submit(q) {
      q = String(q || "").trim().slice(0, 1000);
      if (!q || busy) return;
      input.value = "";
      busy = true;
      send.disabled = true;
      if (chips.parentNode) chips.parentNode.removeChild(chips);
      add("ai-user", q);
      // Settled before the question joins the history: the free reply to a
      // clarification the assistant asked costs nothing.
      var free = nextMessageIsFree();
      var countsBefore = workerCounts;
      var thinking = add("ai-bot ai-thinking", "Reading the data...");
      try {
        var answer = await ask(q);
        thinking.className = "ai-msg ai-bot";
        thinking.innerHTML = render(answer);
        // Counted once answered, as the Worker counts it. When the Worker sent
        // its own figure that has already been written; this covers a reply
        // that carried none.
        if (!free && workerCounts === countsBefore) writeUsed(readUsed() + 1);
        renderLimit();
      } catch (err) {
        thinking.className = "ai-msg ai-bot ai-error";
        thinking.textContent = (err && err.message) ? err.message
          : "Sorry, that question could not be answered. Please try again, or ask it a different way.";
        // Back in the box, so trying again is one press rather than retyping.
        if (!input.value) input.value = q;
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
    // The examples, the tool layer's wording and the answering service's own
    // instructions are written for London's wards and LAs, so outside London
    // (the England view included) there is no button and no panel, rather
    // than one that promises answers about places it cannot describe.
    if (!IS_LONDON) return;
    var host = document.querySelector(".map-wrap");
    if (!host || document.getElementById("ai-panel")) return;

    var panel = document.createElement("div");
    panel.id = "ai-panel";
    // A named region, so it is listed as a landmark and announced by name
    // when focus moves into it.
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-labelledby", "ai-title");

    var head = document.createElement("div");
    head.id = "ai-head";
    var heading = document.createElement("span");
    heading.id = "ai-title";
    heading.textContent = "Ask about this data";
    var close = document.createElement("button");
    close.id = "ai-close";
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    // The cross from the page's icon set (the symbols at the top of index.html).
    close.innerHTML = '<svg class="ic" aria-hidden="true" focusable="false"><use href="#i-x"/></svg>';

    // The remaining count, beside the panel's own name. Built here rather than
    // in mount() because the header outlives the body: mounting is a no-op the
    // second time, but the header is there from the moment the panel exists.
    var limitBadge = document.createElement("span");
    limitBadge.id = "ai-limit";
    limitBadge.className = "ai-limit";
    var headLeft = document.createElement("span");
    headLeft.className = "ai-head-l";
    headLeft.appendChild(heading);
    headLeft.appendChild(limitBadge);

    head.appendChild(headLeft);
    head.appendChild(close);
    limitEl = limitBadge;
    renderLimit();

    var body = document.createElement("div");
    body.id = "ai-body";
    panel.appendChild(head);
    panel.appendChild(body);

    var toggle = document.createElement("button");
    toggle.id = "ai-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    // No "(AI)" suffix on the button. What answers is still declared, but by
    // the panel itself: it opens saying which model writes the answers and
    // where the figures come from, and it carries the verification note under
    // the input the whole time it is open. A parenthetical on the button was a
    // third statement of the same thing in the smallest and least useful place
    // to put it.
    toggle.textContent = "Ask about this data";

    host.appendChild(panel);
    host.appendChild(toggle);

    /* On a phone the panel is the whole screen (see the CSS), so everything
       under it is made inert while it is open: Tab stays in the panel rather
       than walking into the hidden header and map. Each element's own inert
       state is put back on close, since the drawer sets its own. */
    var fullScreen = window.matchMedia ? window.matchMedia("(max-width: 640px), (max-height: 500px)") : null;
    var underneath = [];
    function setUnderneath(on) {
      underneath.forEach(function (u) { u[0].inert = u[1]; });
      underneath = [];
      if (!on) return;
      var els = Array.prototype.slice.call(document.querySelectorAll(".skip-link, .hdr, .mode-rail, #sidebar, footer.sbar, #sb-scrim"));
      Array.prototype.forEach.call(host.children, function (el) { if (el !== panel) els.push(el); });
      els.forEach(function (el) { underneath.push([el, !!el.inert]); el.inert = true; });
    }

    function open() {
      panel.classList.add("open");
      if (fullScreen && fullScreen.matches) setUnderneath(true);
      toggle.hidden = true;
      toggle.setAttribute("aria-expanded", "true");
      mount(body);
      var input = document.getElementById("ai-input");
      if (input) input.focus();
    }

    function shut() {
      panel.classList.remove("open");
      setUnderneath(false);
      toggle.hidden = false;
      toggle.setAttribute("aria-expanded", "false");
      toggle.focus();
    }

    // "Ask about this ward" on the ward sheet opens the panel with the ward
    // named in the box, for the reader to send or change. Nothing is sent
    // for them, because each question counts towards the daily limit.
    openWith = function (text) {
      open();
      var input = document.getElementById("ai-input");
      if (input && text) {
        input.value = text;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
        try { input.setSelectionRange(text.length, text.length); } catch (e) { /* not a text field */ }
      }
    };

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
