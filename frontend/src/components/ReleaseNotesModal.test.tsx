import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it,expect,vi } from "vitest";
import { ReleaseNotes } from "./ReleaseNotesModal";
import { openPublicUrl } from "../services/productSupport";
vi.mock("../services/productSupport", async original => ({...await original<object>(),openPublicUrl:vi.fn().mockResolvedValue(undefined)}));
it("renders headings, bullets, bold, code and opens safe links in the system browser", async () => {render(<ReleaseNotes markdown={'# 제목\n\n본문 **강조** `code`\n\n- 항목\n\n[공지](https://example.com)'} />); expect(screen.getByRole("heading",{name:"제목"})).toBeTruthy(); expect(screen.getByText("강조").tagName).toBe("STRONG"); expect(screen.getByText("code").tagName).toBe("CODE"); expect(screen.getByRole("listitem")).toBeTruthy(); await userEvent.click(screen.getByRole("link",{name:"공지"})); expect(openPublicUrl).toHaveBeenCalledWith("https://example.com"); });
it("does not execute raw HTML or permit unsafe links or remote images", () => { const result=render(<ReleaseNotes markdown={'<script>alert(1)</script>\n\n[bad](javascript:alert) [file](file:///tmp/secret)\n\n![tracking](https://example.com/private.png)'} />); expect(result.container.querySelector("script")).toBeNull(); expect(screen.queryAllByRole("link")).toHaveLength(0); expect(result.container.querySelector("img")).toBeNull(); });
