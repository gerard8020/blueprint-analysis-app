# Civil Takeoff

A calibrated, click-to-measure quantity takeoff tool for civil/CAD blueprints.

The key idea: **measurements come from geometry you draw on a calibrated
drawing, not from an AI guess.** The AI is optional and only suggests *what*
to measure — you stay in control of every number.

## Features

- **PDF and image** support (PDFs rendered with PyMuPDF; multi-page).
- **Scale calibration** — draw a line over a known dimension, enter its real
  length, and every measurement converts to real-world units.
- **Three measurement types**
  - *Linear* — draw line segments (e.g. pipe runs, curb). Reported in `LF`.
  - *Area* — draw rectangles or polygons (e.g. pads, paving). Reported in `SF`,
    or as a **volume** (`CY`) if you give a depth — handy for earthwork.
  - *Count* — drop points (e.g. catch basins, inlets). Reported in `EA`.
- **Waste / contingency %** per line item.
- **Editable takeoff table** with totals grouped by unit.
- **Export** to Excel (Takeoff + Totals sheets) or CSV.
- **Optional AI assist** — GPT-4o suggests likely line items to measure.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## How to get an accurate takeoff

1. Upload the drawing.
2. **Calibrate first.** Pick a dimension you know (a grid line, a scale bar,
   a labeled length) and draw a line exactly along it, then enter its real
   length. Recalibrate whenever you change page or zoom.
3. Measure each item. For multi-segment runs, draw several line segments —
   they are summed. For irregular areas use the polygon tool.
4. Review the table, adjust waste %, and export.

> Calibration is per-view. Keep the drawing at the same on-screen size between
> calibrating and measuring (the app handles this automatically as long as you
> don't switch pages without recalibrating).

## Notes

- `app.py` is the new takeoff tool. `blueprints.py` is the original
  proof-of-concept (AI reads quantities directly — kept for reference, but not
  measurement-grade).
- The AI assist needs an OpenAI API key, entered in the sidebar at runtime.
