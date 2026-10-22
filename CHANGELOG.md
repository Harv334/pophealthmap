# Changelog

## Unreleased

These changes are written and tested, and reach the site with the next data refresh.

Census percentages for wards and LAs are calculated from the summed counts of their output areas: the households with no car or van in a ward's output areas, for example, divided by all their households. A measure of households is therefore a share of households rather than an average weighted by residents. Each indicator card, the Area profile, the Data export notes and the methodology page describe whichever method built the figures on screen, so they change with the data.

Ask is now on each region's map as well as London's. It is told which region's map is open, so it ranks and compares that region's own areas, and its example questions there name no places. The England map still has no Ask panel, because it holds too many areas to look up at once.

## 1.0.0, 16 October 2026

This is the first full release of PopHealth Map, a free map of population health with no sign-in. It puts small-area health, deprivation and Census figures on the same map as the GP practices, pharmacies and charities that serve those places, explains any area in plain English, and lets you build, compare and download your own areas. The site keeps its Beta badge, because the figures are still provisional while the site is being checked.

The map covers London in full, with regional packs for other parts of England and an England view. It draws every figure at the levels it can honestly be shown at, from the local authority (LA) down through wards, MSOAs and LSOAs to Census output areas of about 125 households, and the level switch offers only the levels each indicator supports. Every figure is placed on the 2021 Census areas first and then combined into wards and LAs with the official ONS lookups: counts are summed, and rates, including Census percentages, are weighted by population. Figures that are published only for a whole LA are labelled as such rather than repeated as though they described each ward.

The Indicators tab holds more than a hundred measures from open government and NHS sources. They include the Index of Multiple Deprivation 2025 and its domains, the Census 2021 topic summaries on health, disability, age, ethnicity, housing, tenure, work, qualifications, travel and unpaid care, OHID's Local Health and public health profiles, QOF disease prevalence, recorded crime, the claimant count, fuel poverty, air quality, green space and public transport access. Each indicator has a card that says what is counted and out of what, the level it is published at and how other levels are calculated from it, the period it covers, any caution about reading it, how it compares with England where the publisher says, and when it is next updated, with a link to its row on the methodology page. Two indicators can be shown together on one map, and the map colours a figure as better or worse only where it is clear which end is better.

The tools sit along the top of the sidebar. Explore is the map itself, with search by place, postcode, ward, LA or ICB. Query finds the wards that meet the conditions you set, Compare puts areas side by side, Custom area adds up any group of LSOAs you choose, and Data export downloads any level as a spreadsheet with a notes file that cites every source. Selecting an area opens its Area profile and a plain English summary, and any ward can be opened as a printable report. In London, Ask answers questions about the figures in plain English, using the same data the map shows.

The Layers tab adds GP practices, pharmacies, dentists, hospitals, primary care network catchments and green spaces, and the map can be shown over a street map or a satellite background. The Charities tab shows local voluntary and community organisations by cause, and the Directory lists practices, pharmacies and charities near a postcode or within a ward.

The site aims to meet WCAG 2.2 AA, and its accessibility statement lists where it still falls short. Everything the map shows can also be reached by keyboard through search, Query, the Directory and Data export, and the site works on phones and on desktops. Its methodology, accessibility and privacy pages explain how the figures are made, how the site can be used and what it does not collect. Every pull request and every deploy runs an automated browser test suite with accessibility checks on the map and the pages around it, and a check that no dash has crept into the text people read.
