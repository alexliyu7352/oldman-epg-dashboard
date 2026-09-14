import { Component } from "oldman-web/core";
import { EventStreamClient } from "oldman-web/sse";

interface RealtimeTableRow {
  id: string | number;
  cells: Record<string, string>;
}

interface RealtimeTablePayload {
  rows: RealtimeTableRow[];
}

/** Apply page-owned SSE values to matching visible Table cells. */
export class RealtimeTable extends Component {
  static readonly componentName = "realtime-table";

  override async mount(): Promise<void> {
    const url = this.root.dataset.omRealtimeUrl;
    if (!url) throw new Error("Realtime Table requires data-om-realtime-url");

    const client = new EventStreamClient(url);
    client.on<unknown>("examples.table.metrics", (payload) => this.apply(payload));
    this.cleanup(() => client.close());
  }

  private apply(payload: unknown): void {
    if (!isRealtimeTablePayload(payload)) {
      this.logger.error("Ignored invalid realtime Table payload", payload);
      return;
    }

    for (const update of payload.rows) {
      const row = this.root.querySelector<HTMLElement>(
        `[data-om-table-row-id="${CSS.escape(String(update.id))}"]`
      );
      if (!row) continue;

      for (const [name, value] of Object.entries(update.cells)) {
        const cell = row.querySelector<HTMLElement>(`[data-om-column="${CSS.escape(name)}"]`);
        if (cell) cell.textContent = value;
      }
    }
  }
}

function isRealtimeTablePayload(payload: unknown): payload is RealtimeTablePayload {
  if (!isRecord(payload) || !Array.isArray(payload.rows)) return false;
  return payload.rows.every((row) =>
    isRecord(row)
    && (typeof row.id === "string" || (typeof row.id === "number" && Number.isInteger(row.id)))
    && isRecord(row.cells)
    && Object.values(row.cells).every((value) => typeof value === "string")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
