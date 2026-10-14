# Third-party notices

TTSkyAnnot is free software under the GNU General Public License, version 3 or (at your option) any later
version (see `LICENSE`). It bundles the data and libraries listed below. Each keeps its own licence.

## Bundled data (`data/`)

| File | Source | Licence | Notes |
|---|---|---|---|
| `openngc/NGC.csv`, `openngc/addendum.csv` | [OpenNGC](https://github.com/mattiaverga/OpenNGC) by Mattia Verga, commit `4e2398a` (2026-10-10) | CC BY-SA 4.0 (`openngc/CC-BY-SA-4.0.txt`) | Unmodified. Messier/NGC/IC positions, sizes, position angles, names. |
| `openngc/outlines_stellarium.dat` | OpenNGC `outlines/`, same commit | CC BY-SA 4.0 | Unmodified. Nebula outlines traced on DSS2 with Aladin Sky Atlas (CDS, Strasbourg). |
| `stellarium/outlines.dat` | [Stellarium](https://github.com/Stellarium/stellarium) `nebulae/default/outlines.dat`, commit `e8f5283` (2026-06-16) | GPL-2.0-or-later | Unmodified. Used only for objects that OpenNGC does not outline. The file does not state its original author; if you hold rights to it and object to its use, please open an issue and it will be removed. Copyright © the Stellarium contributors. |
| `iau/IAU-CSN.txt` | [IAU Catalog of Star Names](https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt), IAU Division C Working Group on Star Names, version 2022-04-04 | CC BY (IAU) | Unmodified. Official IAU star names; see https://www.iau.org/public/themes/naming_stars/ |
| `vizier/bsc5_V50.csv` | Yale Bright Star Catalogue, 5th revised ed. (Hoffleit & Warren 1991), VizieR V/50, exported 2026-10-14 | VizieR / CDS terms (free use with acknowledgement) | Columns HR, Name, HD, RAJ2000, DEJ2000, Vmag, B-V, SpType, Parallax. |
| `vizier/sharpless_VII20.csv` | Catalogue of HII Regions (Sharpless 1959), VizieR VII/20, exported 2026-10-14 | VizieR / CDS terms | J2000 positions computed by VizieR. |

Queried at run time (not bundled), cached locally: Hipparcos main catalogue (VizieR I/239), Tycho-2 (VizieR
I/259), Gaia DR3 (VizieR I/355/gaiadr3). This research has made use of the VizieR catalogue access tool, CDS,
Strasbourg, France. This work has made use of data from the European Space Agency (ESA) mission Gaia
(https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC).

Online plate solving uses https://ttastro.com (only detected star coordinates are sent) and, if you configure an
API key, https://nova.astrometry.net (a reduced, non-public JPEG is uploaded). ASTAP is used if you install it
yourself; it is not bundled.

## Libraries (Windows build)

| Library | Licence |
|---|---|
| Python | PSF License |
| PyQt5 | GPL-3.0 (Riverbank Computing) |
| Qt 5 | LGPL-3.0 (The Qt Company); its source is available from https://download.qt.io |
| NumPy | BSD-3-Clause |
| SciPy | BSD-3-Clause |
| Astropy (and pyerfa) | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| tifffile | BSD-3-Clause |
| lz4 | BSD-3-Clause |
| requests, urllib3, idna, charset-normalizer, certifi | Apache-2.0 / MIT / BSD / MPL-2.0 (certifi) |
| PyInstaller bootloader | GPL-2.0 with a bootloader exception |

## Code

`skyannot/starfind.py` is the PhotoEdit star finder and `skyannot/xisfio.py` the XISF reader/writer from
TTAstroPiLot, both by the author of TTSkyAnnot and released here under GPL-3.0-or-later. `xisfio.py` was written
from PixInsight's public XISF 1.0 specification.
