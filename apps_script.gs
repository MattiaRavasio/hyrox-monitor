/**
 * HYROX Monitor → Google Sheets bridge.
 *
 * Setup:
 *   1. Open the Google Sheet you want to use.
 *   2. Extensions → Apps Script. Replace Code.gs with this file.
 *   3. Project Settings → Script properties → add property:
 *        SHARED_SECRET = <pick any random string, e.g. `openssl rand -hex 16`>
 *   4. Deploy → New deployment → Type: Web app
 *        Execute as: Me
 *        Who has access: Anyone
 *      → Deploy. Copy the Web App URL.
 *   5. Add GitHub Actions secrets to mattiaravasio/hyrox-monitor:
 *        GOOGLE_SHEETS_WEBAPP_URL = <the Web App URL>
 *        GOOGLE_SHEETS_SECRET     = <same string as SHARED_SECRET above>
 */

const SHEET_NAME = 'Races';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const expected = PropertiesService.getScriptProperties().getProperty('SHARED_SECRET');
    if (!expected || body.secret !== expected) {
      return jsonResponse({ error: 'unauthorized' });
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sheet = ss.getSheetByName(SHEET_NAME);
    if (!sheet) sheet = ss.insertSheet(SHEET_NAME);

    const headers = [
      'Label', 'Title', 'City', 'Race Dates', 'Status',
      'Drops in (days)', 'Sales close in (days)', "Men's Open",
      'Buy Tickets', 'Race Page',
    ];

    const daysUntil = (iso) => {
      if (!iso) return '';
      const ms = new Date(iso).getTime() - Date.now();
      return Math.ceil(ms / (1000 * 60 * 60 * 24));
    };

    const fmtMensOpen = (r) => {
      if (r.mens_open_buyable === true) return 'AVAILABLE';
      if (r.status === 'on_sale' && r.mens_open_exists && !r.mens_open_active) return 'SOLD OUT';
      return '—';
    };

    const rows = (body.races || []).map(r => [
      r.label || '',
      r.title || '',
      r.city_code || '',
      r.race_dates || '',
      (r.status || '').toUpperCase(),
      r.drops_in_days != null ? r.drops_in_days : '',
      r.status === 'on_sale' ? daysUntil(r.sell_end) : '',
      fmtMensOpen(r),
      r.vivenu_url || '',
      r.link || '',
    ]);

    sheet.clear();
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    if (rows.length) {
      sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);
    }
    sheet.setFrozenRows(1);
    sheet.autoResizeColumns(1, headers.length);

    const stampRow = (rows.length || 0) + 3;
    sheet.getRange(stampRow, 1).setValue('Last updated:');
    sheet.getRange(stampRow, 2).setValue(body.updated_at || new Date().toISOString());
    sheet.getRange(stampRow, 1, 1, 2).setFontStyle('italic').setFontColor('#666666');

    return jsonResponse({ ok: true, rows: rows.length });
  } catch (err) {
    return jsonResponse({ error: String(err) });
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return jsonResponse({ ok: true, hint: 'POST JSON to this endpoint with a valid secret.' });
}
