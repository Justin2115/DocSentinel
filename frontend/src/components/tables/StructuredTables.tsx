import type { ReactNode } from "react";

import type { ExtractedTableCell, ExtractedTable } from "../../api/document";
import { formatConfidence } from "../../utils/confidence";

// The reader indexes a measured table as "[table p3]" followed by "cell | cell"
// lines. Those markers are how the index knows a line is a row, not how a person
// should read it - see `stripTableBlocks`.
const TABLE_BLOCK_MARKER = /^\[(?:scanned-)?table p\d+\]$/;

/**
 * Remove the "cell | cell" rows that are about to be rendered as a real table.
 *
 * Only called when the page has stored structures: with none, the raw text stays
 * exactly as it was read, markers included, and that is the fallback.
 */
export const stripTableBlocks = (text: string): string => {
	const kept: string[] = [];
	let skipping = false;
	for (const line of text.split("\n")) {
		if (TABLE_BLOCK_MARKER.test(line.trim())) {
			skipping = true;
			continue;
		}
		if (skipping) {
			if (!line.trim()) {
				skipping = false;
				kept.push(line);
			}
			continue;
		}
		kept.push(line);
	}
	return kept.join("\n").trim();
};

type Slot = ExtractedTableCell | "covered" | null;

const gridOf = (table: ExtractedTable): Slot[][] => {
	let rows = table.row_count ?? 0;
	let cols = table.col_count ?? 0;
	for (const cell of table.cells) {
		rows = Math.max(rows, cell.row + Math.max(1, cell.rowspan));
		cols = Math.max(cols, cell.col + Math.max(1, cell.colspan));
	}
	const grid: Slot[][] = Array.from({ length: rows }, () =>
		Array.from({ length: cols }, () => null as Slot)
	);
	for (const cell of table.cells) {
		if (cell.row < 0 || cell.col < 0 || cell.row >= rows || cell.col >= cols) {
			continue;
		}
		grid[cell.row][cell.col] = cell;
		for (let down = 0; down < Math.max(1, cell.rowspan); down += 1) {
			for (let right = 0; right < Math.max(1, cell.colspan); right += 1) {
				if (!down && !right) continue;
				if (cell.row + down < rows && cell.col + right < cols) {
					grid[cell.row + down][cell.col + right] = "covered";
				}
			}
		}
	}
	return grid;
};

const hasText = (cell: ExtractedTableCell) =>
	Boolean(cell.text.trim()) || cell.uncertain;

// A row with nothing readable and nothing flagged in it is a gap in the ruling,
// not a row of the table - the same rule the indexed text applies, so the viewer
// and the search evidence show the same rows.
const rowIsVisible = (row: Slot[]) =>
	row.some((slot) => slot && slot !== "covered" && hasText(slot));

/**
 * One stored table, drawn with the rows, columns, spans and header rows the
 * reader measured. Cells it could not resolve stay visibly empty and marked:
 * nothing is filled in here.
 */
export const StructuredTable = ({
	table,
	highlight,
}: {
	table: ExtractedTable;
	highlight?: (text: string) => ReactNode;
}) => {
	const headerRows = new Set(table.header_rows ?? []);
	const grid = gridOf(table);
	const render = (text: string): ReactNode =>
		highlight ? highlight(text) : text;

	const rowElement = (rowIndex: number) => {
		const row = grid[rowIndex];
		if (!row || !rowIsVisible(row)) return null;
		const isHeader = headerRows.has(rowIndex);
		return (
			<tr key={rowIndex}>
				{row.map((slot, colIndex) => {
					if (slot === "covered") return null;
					if (!slot) {
						return <td key={colIndex} className="ocrCellEmpty" />;
					}
					const cellClass = [
						slot.uncertain ? "ocrCellUncertain" : "",
						slot.text.trim() ? "" : "ocrCellEmpty",
					]
						.filter(Boolean)
						.join(" ");
					const title = slot.uncertain
						? "The reader could not tell what belongs in this cell. Nothing has been filled in."
						: typeof slot.confidence === "number"
							? formatConfidence(slot.confidence) + " word confidence"
							: undefined;
					const content =
						slot.uncertain && !slot.text.trim()
							? "unreadable"
							: render(slot.text);
					return isHeader || slot.header ? (
						<th
							key={colIndex}
							scope={slot.rowspan > 1 ? "rowgroup" : "col"}
							colSpan={slot.colspan > 1 ? slot.colspan : undefined}
							rowSpan={slot.rowspan > 1 ? slot.rowspan : undefined}
							className={cellClass || undefined}
							title={title}
						>
							{content}
						</th>
					) : (
						<td
							key={colIndex}
							colSpan={slot.colspan > 1 ? slot.colspan : undefined}
							rowSpan={slot.rowspan > 1 ? slot.rowspan : undefined}
							className={cellClass || undefined}
							title={title}
						>
							{content}
						</td>
					);
				})}
			</tr>
		);
	};

	const indices = grid.map((_, index) => index);
	const headRows = indices.filter((index) => headerRows.has(index));
	const bodyRows = indices.filter((index) => !headerRows.has(index));

	return (
		<table className="ocrTable">
			<caption className="ocrTableCaption">
				{(table.row_count ?? grid.length) +
					" x " +
					(table.col_count ?? (grid[0] ? grid[0].length : 0)) +
					" cells"}
				{table.engine ? ` · ${table.engine}` : ""}
				{` · ${
					typeof table.confidence === "number" &&
					Number.isFinite(table.confidence)
						? `${formatConfidence(table.confidence)} cell confidence`
						: "cell confidence not measured"
				}`}
				{table.uncertain_cells
					? ` · ${table.uncertain_cells} unreadable cell(s)`
					: ""}
			</caption>
			{headRows.length > 0 ? (
				<thead>{headRows.map(rowElement)}</thead>
			) : null}
			<tbody>{bodyRows.map(rowElement)}</tbody>
			{table.note ? (
				<tfoot>
					<tr>
						<td
							colSpan={Math.max(1, table.col_count ?? grid[0]?.length ?? 1)}
							className="ocrTableNote"
						>
							{table.note}
						</td>
					</tr>
				</tfoot>
			) : null}
		</table>
	);
};

/** Every stored table of one page, in the order the reader found them. */
export const PageStructuredTables = ({
	tables,
	highlight,
}: {
	tables: ExtractedTable[];
	highlight?: (text: string) => ReactNode;
}) => (
	<div className="ocrTables">
		{tables.map((table) => (
			<StructuredTable key={table.id} table={table} highlight={highlight} />
		))}
	</div>
);
