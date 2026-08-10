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
| PTAL | Greater London Authority, London Datastore | OGL v3 | |
| Street crime | data.police.uk (Home Office) | OGL v3 | |
| Charity register | Charity Commission for England and Wales | OGL v3 | |
| Basemap tiles | OpenStreetMap contributors | ODbL 1.0 | Attribution rendered by the map itself |

## Hand-compiled layers

Some layers are not derived from any published register. They were compiled by
hand for this project and have no upstream licence:

  community centres, ESOL providers, libraries, hospitals

They are released under the same MIT licence as the code, but treat them as
best-effort local knowledge rather than an authoritative register, and check
them before relying on them.

## Personal data

Nothing here is personal data. The organisation records (practices, pharmacies,
charities) are published business contact details, and every statistical layer
is aggregated to LSOA or ward level with the publishers' own disclosure control
already applied.
