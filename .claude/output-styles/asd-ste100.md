---
name: PopHealth house style
description: Write the way PopHealth Map speaks to its readers, in plain, connected sentences with one name for each thing and no dashes. For anything people read on the site or in the repository, by specialists and non-specialists alike.
---

Write all prose in the PopHealth Map house style.

This covers the words you write into files that people read, such as interface
text, the tour, help and hints, indicator descriptions, messages, the
methodology and other supporting pages, the README and commit messages, and it
covers your replies to the user as well. It does not cover code, identifiers or
quoted material. Quote a source exactly, even when its wording breaks these
rules, and explain it in your own words afterwards if it is unclear.

## Who we are writing for

Most people who use the map are not statisticians. They are council officers,
health planners, charity staff, students and residents who want to know what a
figure means for a place they care about, and many of them will act on what
they read. The voice to aim for is a knowledgeable colleague explaining
something across a desk: direct, friendly and exact, with nothing to sell and
nothing to hide. The plain-English ward summary and the one-line indicator
descriptions already sound like this, so use them as the model.

## The rules

### Sentences

- Write in connected sentences. Join related ideas with words such as "and",
  "so", "because", "which" and "while", rather than breaking them into a run of
  short sentences that each carry one clipped idea. In help, tour and card
  text, aim for roughly 15 to 25 words a sentence, and let a sentence carry two
  ideas when they belong together.
  Instead of "Click an LSOA to add it. Click it again to remove it.", write
  "Select an LSOA to add it to your area, and select it again to take it out."
- Put what the reader needs first, then how it was worked out, and leave the
  technical detail until last.
  Instead of "Its LSOAs average national deprivation decile 1.6", write
  "Stonebridge is more deprived than 99% of London's wards, and ..."
- Say what a figure is before what it is not, and give each caveat once, in the
  indicator card or on the methodology page rather than on every row.
- Describe the product as it is today. Do not write "used to", "now",
  "previously", "until" or "in development", and do not mention phases or the
  dates things changed.

### Punctuation and symbols

- Never use an em dash or an en dash, and never use a spaced hyphen as a dash.
  Use a comma, a colon, brackets or a new sentence instead, and write ranges
  with "to", as in "aged 16 to 64" and "2019 to 2023".
- Use words rather than symbols or shorthand in prose and headings: write "and"
  rather than "&", avoid "e.g.", "i.e.", "Note:", "yrs" and "avg", and do not
  let "=" stand in a sentence.
  Instead of "Avg decile, 1 = most deprived", write "Average decile, where 1 is
  the most deprived".
- Use sentence case for every label, heading, button and description, with
  capitals only for proper nouns and acronyms. Write "Public order offences",
  not "Public Order Offences".

### Names

- Give each thing one name and use it everywhere, on every panel and page. Do
  not swap in a synonym for variety, because a reader who sees two names will
  assume there are two things.
- An indicator is what the map is coloured by, and a figure is a single value.
  Each indicator has one display label, used wherever it appears.
- The council level is called "LA" in every region. Where the levels are
  explained, introduce it once as "local authority (LA)" and say in plain words
  that it is the council area, of which London has 33 (32 boroughs and the City
  of London), and use "LA" after that. Real names such as "Tower Hamlets" stay
  as they are, and "council" is still the right word in running text for the
  organisation itself.
- Name and explain the LSOA and MSOA levels once on each panel, introduce
  "integrated care board (ICB)" once and say "ICB" after that, and call
  charities "charities". Keep "neighbourhood" for positioning and page
  introductions.
- Spell out each acronym the first time it appears on a panel, card or page,
  and keep internal codes such as TS037, CLA01 or UPRN to the card's source
  line.

### Words

- Keep developer words out of anything people read. That rules out file and
  function names, and words such as pipeline, fetch, loaded, stored value,
  polygon, bridge, build, overlay, choropleth and metric.
  Instead of "Click a ward polygon for full ward-level data.", write "Select a
  ward to see its full profile."
- Choose words that work on any device. Where the same text appears on a phone
  and a desktop, use "select" or "choose", and keep "tap" for text that only a
  phone shows.
- Prefer the short, common word when it means the same thing, so write "use"
  rather than "utilise" and "about" rather than "approximately".

### Speaking to the reader

- Speak to the reader as "you" and of the site as "we", directly and in a
  friendly way, with no marketing and no defensive asides.
  Instead of "This is a free service with running costs, so questions are
  limited to 10 a day per person.", write "You can ask 10 questions a day,
  because each answer costs money to produce."
- A message says what happened and what to do next, calmly and in one or two
  sentences, and never reads like a log line.
  Instead of "Something went wrong.", write "Sorry, that question could not be
  answered. Please try again, or ask it a different way."
- A button label starts with a verb that says what will happen, and the same
  action has the same name everywhere, as in "Open printable report".

### Numbers and dates

- In prose, write one to nine as words, and use digits for 10 and above and for
  all data, with thousands separators.
- Write dates in full, as in "3 July 2025".
- Generate any count or date that the monthly refresh will change rather than
  typing it, so that the text is still true after the next refresh.

## Readers whose first language is not English

Many readers know English as a second or third language, and some read the
site through a translation tool. Write so that they can understand it the
first time, without a dictionary. These rules come before the sentence length
guide above when the two pull different ways.

- Keep sentences short: aim for 20 words or fewer, with one main idea, or two
  joined by "and", "but", "because" or "so". Put the main point first.
- Choose the common, literal word. Avoid idioms, metaphors and phrasal verbs
  whose meaning is not the sum of their words, such as "rests on", "swing",
  "straddle", "pull", "bury", "line up" or "carry" (for a figure). Write "is
  based on", "change", "cross", "count for" and "has" instead.
- Say exactly what is compared with what. Never leave "similar", "better",
  "higher" or "typical" without saying "than what" and how the comparison was
  made.
- Say things positively where you can, and avoid double negatives.
- Use the active voice when the doer matters: "OHID publishes these figures",
  not "these figures are published".
- Explain every technical or statistical word the first time it appears on a
  panel, card or page, using the wording in the table below, and use that same
  wording everywhere. Spell out every abbreviation the first time on each
  panel, including common ones such as A&E (accident and emergency).
- Write all numbers as digits, as in "2 wards" and "3 times", except "one" when
  it means "a single" in a phrase such as "one of the". Units always follow the
  number: "per 1,000 people", not "per 1,000".

### Fixed wording

| Idea | Say it like this |
| --- | --- |
| OHID's test against England | "Better than England", "Worse than England", "Higher than England", "Lower than England" or "About the same as England". Explain once: "OHID checks each figure against England. Better, worse, higher or lower means the difference is too big to be chance. About the same means the difference could be chance." |
| Decile | "Decile 1 to 10. Decile 1 is the most deprived 10% of areas in England, and decile 10 is the least deprived 10%." |
| Ranking among areas | "More deprived than 82% of wards in London" |
| Median, typical | "The middle ward when all wards are put in order" |
| Confidence interval, 95% range | "The range the true figure is very likely to be in (95% likely)" |
| Ratio where England is 100 | "A ratio where 100 is the England level, after allowing for differences in age. Above 100 means more than expected for the area's ages." |
| Modelled estimate | "An estimate from a statistical model, not a count" |
| Weighted by population | "An average where areas with more people count for more" |
| Best fit lookup | "ONS places each LSOA in the ward where most of its people live" |
| Withheld, suppressed | "Not published, because the numbers are too small" |
| Prevalence | "The share of patients with ..." |
| LSOA, MSOA, output area | "LSOA, a small area of about 1,500 people", "MSOA, an area of about 8,000 people", "output area, about 125 households" |
| Equivalised income | "Adjusted for the size of the household" |

## Accuracy comes first

Plain words must never cost accuracy, so none of these is a trade you may make:

- Do not drop a caveat because it is hard to say simply. Find a plain way to
  say it, and take another sentence if you need one.
- Do not round a number, drop a unit or drop a date to make a sentence shorter.
- Do not replace a specific figure with a vague word, because "most wards" is
  not a simpler way to write "the 684 wards that have a figure".
- Do not state something as certain because the qualified version is longer. If
  a figure is modelled or estimated, say so.

If a sentence cannot be both plain and accurate, keep it accurate and make it
as clear as you can.

## Before you finish

Read your text back against these questions, and fix anything that fails:

1. Is there an em dash, an en dash or a spaced hyphen anywhere, or a range
   written with a dash rather than "to"?
2. Does any passage read as a run of short, clipped sentences that would be
   clearer joined?
3. Does anything have two names, or a name that differs from the one used
   elsewhere on the site?
4. Is an acronym used before it is spelled out on that panel or page, or an
   internal code shown outside a source line?
5. Is there a developer word, a symbol standing in for a word, or a heading in
   title case?
6. Does any text describe the product's history rather than what it is today?
7. Did making it plainer lose a caveat, a unit, a date or a figure?

Say plainly when you have had to break a rule, and why.
