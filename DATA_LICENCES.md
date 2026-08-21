# Data licences and attribution

The code in this repository is MIT licensed (see LICENSE). The data is not.
Nearly all of it is published under the **Open Government Licence v3.0**,
which permits reuse including commercially, but **requires attribution**. That
requirement is not satisfied by the MIT licence, so it is set out here and
mirrored in the map's about panel.

If you reuse this project's outputs, carry these attributions with them.

## Required attribution statements

**Census 2021** has its own wording that must be reproduced:

> Source: Office for National Statistics licensed under the Open Government
> Licence v.3.0

**Ordnance Survey** derived boundary and greenspace data:

> Contains OS data © Crown copyright and database right 2026
> Contains Royal Mail data © Royal Mail copyright and database right 2026
> Contains National Statistics data © Crown copyright and database right 2026

**Transport for London** is the one source here that is *not* under OGL v3. It
is licensed under **version 2.0 of the Open Government Licence with specific
amendments for Transport for London**, and it requires all three of these
statements to be reproduced:

> Powered by TfL Open Data
>
> Contains OS data © Crown copyright and database rights 2016
>
> Geomni UK Map data © and database rights [2019]

TfL's terms state that failure to comply with the attribution conditions
terminates the licence automatically, so these three lines are not optional and
must travel with any reuse of the station and stop measures. The same terms cap
requests at 500 per minute per feed; the pipeline makes 43 calls per run and
paces itself well inside that.

**Defra UK-AIR** prescribes its own citation, which should be reproduced:

> © Crown copyright Defra via uk-air.defra.gov.uk, licenced under the Open
> Government Licence (OGL).

The PCM landing page also asks, more loosely, that you "acknowledge Defra as
the source for this data if using the maps for your work". Name both Defra and
uk-air.defra.gov.uk; the site asks for the domain as well as the department.

## Source by source

| Data | Publisher | Licence | Notes |
|------|-----------|---------|-------|
| Census 2021 topic summaries | ONS, via Nomis | OGL v3 | Use the ONS wording above verbatim |
| Indices of Deprivation 2025 | MHCLG | OGL v3 | File 7, filtered; see RAW_DATA_SOURCES.md |
| Boundaries (LSOA, ward, LAD) | ONS Open Geography Portal | OGL v3 | Contains OS and National Statistics data |
| LSOA to ward best-fit lookup | ONS Open Geography Portal | OGL v3 | |
| Postcode geography | postcodes.io, over the ONS Postcode Directory | OGL v3 | postcodes.io itself is MIT; the underlying ONSPD is OGL v3 and carries the OS and Royal Mail notices above |
| GP practices (epraccur) | NHS England Organisation Data Service | OGL v3 | |
| Pharmacies (edispensary) | NHS England Organisation Data Service | OGL v3 | |
| QOF prevalence, public health outcomes | OHID Fingertips | OGL v3 | |
| Claimant count, DWP benefits | DWP and ONS, via Nomis | OGL v3 | |
| Sub-regional fuel poverty | DESNZ | OGL v3 | |
| Access to green and blue space | Defra, with ONS, OS and Natural England | OGL v3 | Official statistic in development |
| Background air quality (NO₂, PM2.5, PM10) | Defra UK-AIR, Pollution Climate Mapping | OGL v3 | Modelled, not measured. Acknowledge Defra as the source, per the wording above |
| Stops and stations (distances, counts) | Transport for London, Unified API | **OGL v2 with TfL amendments** | Not OGL v3. Requires all three attribution statements above, or the licence terminates |
| PTAL | Greater London Authority, London Datastore | OGL v3 | TfL's accessibility banding, republished by the GLA in the LSOA Atlas |
| Street crime | data.police.uk (Home Office) | OGL v3 | |
| Charity register | Charity Commission for England and Wales | OGL v3 | |
| NHS trust sites (hospitals) | NHS Organisation Data Service | OGL v3 | |
| Cultural Infrastructure Map | Greater London Authority | OGL v3 | "Contains public sector information licensed under the Open Government Licence v3.0" |
| Basemap tiles | OpenStreetMap contributors | ODbL 1.0 | Attribution rendered by the map itself |

## Hand-compiled layers

None. Every layer on the map now comes from a published register with a licence
of its own, listed above. Five hand-compiled ones (schools, community centres,
libraries, ESOL providers and CICs) and a hand-typed hospital list were removed
in favour of sources that refresh themselves.

## Personal data

Nothing here is personal data. The organisation records (practices, pharmacies,
charities) are published business contact details, and every statistical layer
is aggregated to LSOA or ward level with the publishers' own disclosure control
already applied.
