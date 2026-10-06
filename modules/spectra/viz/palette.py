"""
Trace styling shared by the on-screen plot and the exported figures.

The Spectra page (`live_plot`) and the QC Report's figures (`fit_plot`) draw
the same three things — the data, the total fit, and the residuals — and used to
each pick their own colors. They disagreed: on screen the fit was a black dashed
line over blue points, while the .pptx drew it as a solid orange line over
muted-blue points, so a report didn't look like the spectrum it came from. One
definition here, imported by both, is what keeps them the same.

Colors are hex rather than CSS names ("#0000FF", not "blue") because the .pptx
legend parses them with `core.report.pptx._hex_to_rgb`, which falls back to
black for anything it can't read. A CSS name would still plot correctly and
silently render a black swatch beside it. The values are the CSS names the
on-screen plot used before this module existed: purple, black and green — and
blue for the raw layer, which only that page draws.

Only the traces *both* plotters draw belong here. The Spectra page's own layers
— the raw and de-spiked series, the live previews — are its alone, and stay in
`live_plot`.
"""

# The series that actually gets fitted: de-spiked and baseline-corrected. Both
# surfaces plot exactly this one — the Spectra page as its "Baseline-corrected"
# layer, the report and the exported figures as their only data trace — so both
# have to draw it the same color, or a report doesn't look like the spectrum it
# came from.
#
# The report used to paint it in RAW_COLOR below, which was wrong twice over: it
# named the raw file's color for a series that is not the raw file, and on a
# material whose preset assigns blue to a peak (WSe2's C and center are
# #3276EC) it put the data and those peaks in the same hue, so the preset colors
# stopped reading as preset colors. Purple is what the Spectra page has always
# drawn here.
PROCESSED_COLOR = "#800080"  # CSS "purple"

# The file as read, before de-spiking or baseline removal. The Spectra page's
# first layer and no one else's — the report never draws raw data. It lives here
# anyway so that it can be seen not to collide with the shared colors above.
RAW_COLOR = "#0000FF"  # CSS "blue"

# The summed fit. Solid black since v5.12.0 (dashed before): the components are
# colored, so black alone tells the fit apart from them, and a solid line reads
# better where it sits on top of the data, which is most of its length.
FIT_TOTAL_COLOR = "#000000"  # CSS "black"
FIT_TOTAL_DASH = "solid"

# Data minus fit. Green, and never the same color as the fit or the data, since
# the whole point of the panel is telling them apart.
RESIDUAL_COLOR = "#008000"  # CSS "green"

# The subtracted baseline, drawn when the fit is shown sitting on the
# background (v5.13.0) — the Spectra page's "Peaks on background" view and the
# Quick Export that mirrors it. Gray: it is context, not a result, and a hue
# here would compete with the preset colors the components are drawn in.
BACKGROUND_COLOR = "#808080"  # CSS "gray"

# Individual fitted peaks keep their own `FittedPeak.color`; only the style is
# shared. Solid, so the dashed total fit reads as the distinct thing, and
# semi-transparent so overlapping components stay legible.
COMPONENT_DASH = None
COMPONENT_OPACITY = 0.7
