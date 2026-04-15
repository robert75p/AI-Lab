╔══════════════════════════════════════════════════════════════════╗
║               TRABA AI LABS — DEPLOYMENT GUIDE                  ║
╚══════════════════════════════════════════════════════════════════╝

WHAT'S INCLUDED
───────────────
  index.html          → Main landing page
  serve.mjs           → Local web server (Node.js)
  pages/
    ai-tools.html     → JSON Builder, Diagram Builder, FIX Tag Lookup,
                        Interactive Data (3D gesture-controlled nodes)
    ai-topics.html    → Knowledge graph / topics explorer
    ai-robot.html     → AI robot page
    ai-pattern.html   → AI pattern page
    jira-ai.html      → JIRA AI automation page
  data/
    xianxia_flat.json → Node data for the Interactive Data visualisation
    customFields.json → Custom field definitions
  vendor/             → ALL libraries bundled — NO internet required
    tailwind.js               Tailwind CSS (full JIT build)
    three.min.js              Three.js 0.160.0
    tf.min.js                 TensorFlow.js 4.17.0
    hand-pose-detection.min.js  TF hand pose model 2.0.1
    fonts.css                 Local font declarations
    fonts/                    Font files (Poppins, Inter, JetBrains Mono)


PRE-REQUISITES
──────────────
  • Node.js v18 or later  ← the ONLY thing you need to install
    Download from: https://nodejs.org  (choose "LTS" version)
    To verify it's installed, open a terminal and run:
      node --version
    You should see something like: v22.x.x

  Everything else (Tailwind, Three.js, TensorFlow, fonts) is
  already bundled in the vendor/ folder. No internet required.


STEP-BY-STEP DEPLOYMENT
────────────────────────

  STEP 1 — Copy the folder
    Copy the entire TRABA-LABS folder to wherever you want on the
    target desktop (e.g. C:\Projects\TRABA-LABS or ~/TRABA-LABS).

  STEP 2 — Open a terminal in that folder
    Windows: Right-click the folder → "Open in Terminal"
             or open Command Prompt / PowerShell and run:
               cd C:\path\to\TRABA-LABS
    Mac/Linux: Open Terminal, then:
               cd /path/to/TRABA-LABS

  STEP 3 — Start the server
    Run this command:
      node serve.mjs

    You should see:
      AI Labs dev server running at http://localhost:3000

    Leave this terminal window open while using the site.

  STEP 4 — Open in browser
    Open any modern browser (Chrome recommended) and go to:
      http://localhost:3000

  STEP 5 — Navigate the pages
    From the main page, click any module tile to open a sub-page.
    All pages work fully offline — no internet required.


INTERACTIVE DATA TAB — GESTURE CONTROL
───────────────────────────────────────
  Located in: AI Tools → INTERACTIVE DATA tab

  Requires: a webcam connected to your desktop.

  Click the tab — it will ask for webcam permission the first time.
  Allow it. Then use hand gestures in front of your webcam:

  Gesture              Label shown on screen
  ───────────────────────────────────────────────────────────────
  No hands             ✦ IDLE FORMATION — NO HANDS DETECTED
  Open palm (1 hand)   Fan arc — 180° spread
  Fist (1 hand)        Gather / regroup
  Point (1 hand)       Beam toward finger direction
  V-sign (1 hand)      90° arc rotation sweep
  Thumb up (1 hand)    Bonus node flies front & center, all others scatter
  Both palms far       Disperse — scatter sphere
  Both palms close     Vortex — spiral helix
  Wrists together      Merge — single giant column

  Keyboard shortcut:
    H  →  Show / hide the gesture reference panel on screen

  Maximize button (top-right of the panel) → full-screen mode


STOPPING THE SERVER
───────────────────
  In the terminal where serve.mjs is running, press:
    Ctrl + C

  If port 3000 is already in use (error: EADDRINUSE), run:
    Windows:   npx kill-port 3000
    Mac/Linux: lsof -ti:3000 | xargs kill


TROUBLESHOOTING
───────────────
  "node is not recognized"
    → Node.js is not installed. See PRE-REQUISITES above.

  Webcam not working
    → Make sure your browser has permission to access the camera.
    → In Chrome: click the camera icon in the address bar → Allow.

  Port 3000 already in use
    → Another instance of the server is running. Either close that
      terminal, or run: npx kill-port 3000  then restart.

──────────────────────────────────────────────────────────────────
  TRABA AI Labs — Built April 2026
──────────────────────────────────────────────────────────────────
