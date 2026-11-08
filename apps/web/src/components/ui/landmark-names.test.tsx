import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTableV2 } from "@/components/console/DataTableV2";
import { Card } from "@/components/ui/Card";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { duplicateLandmarks } from "@/test/landmarks";
import { renderWithIntl } from "@/test/render";

/**
 * WA-03 (NFR-A11Y-001; axe landmark-unique): a table's scroll region stays focusable for
 * keyboard scrolling (axe scrollable-region-focusable), but inside a section that already
 * carries the same name it gets a distinct one, so the landmark list never reads
 * "Schools region, Schools region". Without such a parent nothing changes.
 */
const rows = [{ id: "1", name: "Sample School A" }];

function V2({ caption }: { caption: string }) {
  return (
    <DataTableV2
      id="test.landmarks"
      caption={caption}
      captionHidden
      columns={[{ key: "name", header: "Name", cell: (row: (typeof rows)[number]) => row.name }]}
      state={{ status: "ready", data: rows }}
      rowKey={(row) => row.id}
      emptyTitle="None"
    />
  );
}

function Plain({ label }: { label: string }) {
  return (
    <TableScroll label={label}>
      <Table>
        <THead>
          <Tr>
            <Th>Name</Th>
          </Tr>
        </THead>
        <TBody>
          <Tr>
            <Td>Sample School A</Td>
          </Tr>
        </TBody>
      </Table>
    </TableScroll>
  );
}

describe("table scroll regions have unique landmark names (WA-03)", () => {
  it("DataTableV2 inside a card with the same title", async () => {
    renderWithIntl(
      <Card title="Schools">
        <V2 caption="Schools" />
      </Card>,
    );
    expect(await duplicateLandmarks()).toEqual([]);
    const region = screen.getByRole("region", { name: "Schools table, scrolls sideways" });
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("TableScroll inside a section labelled by a heading with the same text", async () => {
    renderWithIntl(
      <section aria-labelledby="h">
        <h2 id="h">Schools</h2>
        <Plain label="Schools" />
      </section>,
    );
    expect(await duplicateLandmarks()).toEqual([]);
    expect(screen.getByRole("region", { name: "Schools table, scrolls sideways" })).toHaveAttribute(
      "tabindex",
      "0",
    );
  });

  it("keeps the caption as the name when no parent has it", async () => {
    renderWithIntl(
      <>
        <V2 caption="Schools" />
        <Plain label="Invoices" />
      </>,
    );
    expect(await duplicateLandmarks()).toEqual([]);
    expect(screen.getByRole("region", { name: "Schools" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("region", { name: "Invoices" })).toHaveAttribute("tabindex", "0");
  });
});
