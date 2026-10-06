import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, it, expect, vi } from "vitest";
import { FeedbackDialog } from "./FeedbackDialog";
import { submitFeedback } from "../services/productSupport";
vi.mock("../services/productSupport", async original => ({...await original<object>(),submitFeedback:vi.fn()}));
beforeEach(() => vi.clearAllMocks());
async function preview() { await userEvent.type(screen.getByLabelText("제목"),"제목"); await userEvent.type(screen.getByLabelText("내용"),"입력 내용"); await userEvent.click(screen.getByRole("button",{name:"전송 정보 미리보기"})); }
it("previews only consented data and states browser fallback accurately", async () => { vi.mocked(submitFeedback).mockResolvedValue("browser"); render(<FeedbackDialog diagnostics={{platform:"desktop"}} onClose={vi.fn()} />); await preview(); expect(screen.getByText(/다음 정보가 전송됩니다/)).toBeTruthy(); expect(screen.queryByText(/"platform"/)).toBeNull(); await userEvent.click(screen.getByRole("button",{name:"전송 / GitHub 작성 화면 열기"})); expect(await screen.findByText(/GitHub 작성 화면을 열었습니다/)).toBeTruthy(); });
it("keeps the draft on failure and allows retry and edit", async () => { vi.mocked(submitFeedback).mockRejectedValue(new Error("private-error")); render(<FeedbackDialog diagnostics={{}} onClose={vi.fn()} />); await preview(); await userEvent.click(screen.getByRole("button",{name:"전송 / GitHub 작성 화면 열기"})); await screen.findByText("피드백을 전송하지 못했습니다."); expect(screen.getByRole("button",{name:"다시 시도"})).toBeTruthy(); await userEvent.click(screen.getByRole("button",{name:"수정"})); expect(screen.getByLabelText("내용")).toHaveProperty("value","입력 내용"); expect(screen.queryByText("private-error")).toBeNull(); });
it("acknowledges direct submission success", async () => { vi.mocked(submitFeedback).mockResolvedValue("sent"); render(<FeedbackDialog diagnostics={{}} onClose={vi.fn()} />); await preview(); await userEvent.click(screen.getByRole("button",{name:"전송 / GitHub 작성 화면 열기"})); expect(await screen.findByText("고마워요. 피드백이 전송되었습니다.")).toBeTruthy(); });
