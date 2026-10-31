Where the official DFL map goes
===============================
1. Put the map image here, for example  dfl-map.png  (PNG, JPEG, WebP or SVG).
2. Put a file named  map.json  beside it:
     {"file": "dfl-map.png", "alt": "Map of the 48 Diamond Football League clubs", "caption": "Optional caption shown under the map."}
   "file" must be a plain file name (letters, digits, dot, dash, underscore); anything else is ignored.
3. Install the site again (deploy/website/install_site.sh) or copy both files into the live site release.
Until map.json exists the Map page says the official map has not been added. Nothing is drawn in its place.
