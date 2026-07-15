# Backoffice Incidents Analysis

Static frontend for incidents analysis.

## Features

- CSV upload (file picker + drag and drop).
- Visualization of totals, invalid records by type, category breakdown, status breakdown, country breakdown, and satisfaction index.
- Download button for CSV export using API endpoint.

## API dependencies

- `POST /api/inbcidents/analyze`
- `GET /api/incidents/results/export`
