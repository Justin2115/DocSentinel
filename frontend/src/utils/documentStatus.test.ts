import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { dashboardStatusLabel } from "./documentStatus.ts";

describe("dashboardStatusLabel", () => {
	it("keeps processing and review states distinct", () => {
		assert.equal(dashboardStatusLabel("PROCESSING"), "Processing");
		assert.equal(dashboardStatusLabel("PENDING_REVIEW"), "Needs review");
		assert.equal(dashboardStatusLabel("NEEDS_REVISION"), "Needs revision");
	});

	it("does not report rejected or unknown documents as indexed", () => {
		assert.equal(dashboardStatusLabel("REJECTED"), "Rejected");
		assert.equal(dashboardStatusLabel("INDEXED"), "Indexed");
		assert.equal(dashboardStatusLabel("new-backend-state"), "Unknown");
		assert.equal(dashboardStatusLabel(null), "Unknown");
	});
});
