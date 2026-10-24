import type { ReactNode } from "react";

import type { ExtractedTableCell, ExtractedTable } from "../../api/document";
import { isRejectedTable } from "../../api/document";
import { formatConfidence } from "../../utils/confidence";

// The reader indexes a measured table as "[table p3]" followed by "cell | cell"
// lines. Those markers are how the index knows a line is a row, not how a person
// should read it - see `stripTableBlocks`.
const TABLE_BLOCK_MARKER = /^\[(?:scanned-)?table p\d+\]$/;

/**
 * Remove the "cell | cell" rows that are about to be rendered as a real table.
 *
 * Only the pages with a measured grid are rewritten this way: a refused grid has no
 * cells to render, so its page keeps every line the reader recognised.
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
					const score = slot.uncertain
						? "The reader could not tell what belongs in this cell. Nothing has been filled in."
						: typeof slot.confidence === "number"
							? formatConfidence(slot.confidence) + " word confidence"
							: undefined;
					// On a scanned page, text no recognised line fell inside is a reading
					// nothing on the page confirms. A digital text layer is the page's own
					// characters, so the absence of an OCR score says nothing about it.
					const backing =
						slot.text.trim() &&
						slot.from_page === false &&
						table.source !== "digital"
							? " · no line on the page confirmed this text"
							: "";
					const title = score ? score + backing : backing.slice(3) || undefined;
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
				{table.source !== "digital" &&
				table.page_backed_cells === 0 &&
				table.cells.length > 0
					? " · no cell confirmed by the page's own text"
					: ""}
				{table.frame === "corrected"
					? " · cell boxes are measured on a corrected view of the page"
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
		{tables.map((table) =>
			isRejectedTable(table) ? (
				<RejectedTableNotice key={table.id} table={table} />
			) : (
				<StructuredTable
					key={table.id}
					table={table}
					highlight={highlight}
				/>
			)
		)}
	</div>
);

/**
 * A grid the reader measured and refused to show as a table.
 *
 * The shape, the engine and the reason are the measurement, and they are worth
 * reporting; an empty grid is not. The page's own recognised text stays below this,
 * because a refused grid never takes lines out of the page's prose.
 */
export const RejectedTableNotice = ({ table }: { table: ExtractedTable }) => {
	const shape =
		table.row_count && table.col_count
			? `${table.row_count} × ${table.col_count} grid`
			: "grid";
	const reason = (table.note ?? "").replace(/^Not shown as a table:\s*/, "");
	return (
		<div className="ocrTableRefused" role="note">
			<strong>Table not shown</strong>
			<span>
				{shape}
				{table.engine ? ` · ${table.engine}` : ""} · no cells to show ·
				confidence not measured
			</span>
			<p>
				{reason ||
					"the grid could not be checked against the page, so it is not shown as a table."}
			</p>
		</div>
	);
};
