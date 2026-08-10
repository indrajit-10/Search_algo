#!/usr/bin/env python3
"""
Write a multi-sheet .xlsx with the standard library and nothing else.

An .xlsx is a zip of XML parts. Writing one by hand is a hundred lines; adding
openpyxl to a project whose whole promise is "no pip installs" is a dependency
someone has to install on every machine that ever runs this. So: a hundred
lines.

    sheets = [Sheet("By date", ["Date", "Sends"], [["2026-08-01", 1059]])]
    write_xlsx("report.xlsx", sheets)

Strings are written inline rather than through a shared-string table. That
costs a few bytes on repeated values and removes a whole part, its index, and
every chance of the two disagreeing.
"""

import zipfile

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# Style ids written into styles.xml below, referenced by cells as s="N".
PLAIN, HEAD, NUMBER, PERCENT, TOTAL, TOTAL_NUM = 0, 1, 2, 3, 4, 5


class Sheet:
    """One tab: a name, a header row, and rows of values.

    `widths` is per column in characters. A row whose first cell starts with
    "TOTAL" is styled as a total - the rule lives here so that every sheet
    written by this module marks its totals the same way.
    """

    def __init__(self, name, header, rows, widths=None, percent_columns=()):
        self.name = name[:31]
        self.header = header
        self.rows = rows
        self.widths = widths or []
        self.percent_columns = set(percent_columns)


def escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def column_name(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(reference, value, style, percent):
    if value is None or value == "":
        return f'<c r="{reference}" s="{style}"/>'
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)):
        if style == PLAIN:
            style = PERCENT if percent else NUMBER
        elif style == TOTAL:
            style = TOTAL_NUM
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    return (f'<c r="{reference}" s="{style}" t="inlineStr">'
            f"<is><t>{escape(value)}</t></is></c>")


def _sheet_xml(sheet):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           f'<worksheet xmlns="{MAIN}">',
           # Order matters to the schema: sheetViews before cols before
           # sheetData. The frozen pane keeps the header visible while the
           # reader scrolls a year of dates.
           '<sheetViews><sheetView workbookViewId="0">'
           '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews>']
    if sheet.widths:
        cols = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(sheet.widths))
        out.append(f"<cols>{cols}</cols>")
    out.append("<sheetData>")

    cells = "".join(_cell(f"{column_name(i)}1", value, HEAD, False)
                    for i, value in enumerate(sheet.header))
    out.append(f'<row r="1">{cells}</row>')

    for number, row in enumerate(sheet.rows, start=2):
        first = str(row[0]) if row else ""
        style = TOTAL if first.upper().startswith("TOTAL") else PLAIN
        cells = "".join(
            _cell(f"{column_name(i)}{number}", value, style,
                  i in sheet.percent_columns)
            for i, value in enumerate(row))
        out.append(f'<row r="{number}">{cells}</row>')

    out.append("</sheetData>")
    out.append("</worksheet>")
    return "".join(out)


STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{MAIN}">
 <numFmts count="2">
  <numFmt numFmtId="164" formatCode="#,##0"/>
  <numFmt numFmtId="165" formatCode="0.0%"/>
 </numFmts>
 <fonts count="2">
  <font><sz val="11"/><name val="Calibri"/></font>
  <font><b/><sz val="11"/><name val="Calibri"/></font>
 </fonts>
 <fills count="3">
  <fill><patternFill patternType="none"/></fill>
  <fill><patternFill patternType="gray125"/></fill>
  <fill><patternFill patternType="solid"><fgColor rgb="FFEDE6EB"/>
   <bgColor indexed="64"/></patternFill></fill>
 </fills>
 <borders count="2">
  <border><left/><right/><top/><bottom/><diagonal/></border>
  <border><left/><right/><top style="thin"><color rgb="FF8A3A73"/></top>
   <bottom/><diagonal/></border>
 </borders>
 <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
 <cellXfs count="6">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  <xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
  <xf numFmtId="164" fontId="1" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"
      applyFont="1" applyBorder="1"/>
 </cellXfs>
 <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def write_xlsx(path, sheets):
    """Write `sheets` to `path`. Overwrites.

    Every zip entry gets a fixed timestamp. The file lives in git, and a
    workbook whose bytes change on every run because the clock moved would
    show up as a diff on days when no number did.
    """
    if not sheets:
        raise ValueError("an xlsx needs at least one sheet")

    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets)))
    content_types = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{CT}">'
        '<Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{overrides}</Types>")

    root_rels = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{REL}">'
        f'<Relationship Id="rId1" Type="{DOC}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')

    tabs = "".join(f'<sheet name="{escape(s.name)}" sheetId="{i + 1}" '
                   f'r:id="rId{i + 1}"/>' for i, s in enumerate(sheets))
    workbook = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<workbook xmlns="{MAIN}" xmlns:r="{DOC}">'
                f"<sheets>{tabs}</sheets></workbook>")

    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{DOC}/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
    rels += (f'<Relationship Id="rId{len(sheets) + 1}" Type="{DOC}/styles" '
             'Target="styles.xml"/>')
    workbook_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     f'<Relationships xmlns="{REL}">{rels}</Relationships>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        def add(name, text):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text)

        add("[Content_Types].xml", content_types)
        add("_rels/.rels", root_rels)
        add("xl/workbook.xml", workbook)
        add("xl/_rels/workbook.xml.rels", workbook_rels)
        add("xl/styles.xml", STYLES)
        for i, sheet in enumerate(sheets):
            add(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet))
    return path
