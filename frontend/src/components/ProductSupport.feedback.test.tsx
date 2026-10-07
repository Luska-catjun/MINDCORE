import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi, beforeEach } from "vitest";
import { ProductSupport } from "./ProductSupport";
import { submitFeedback } from "../services/productSupport";
vi.mock("@tauri-apps/api/core", () => ({invoke:vi.fn().mockResolvedValue({updater_available:false})}));
vi.mock("./DesktopUpdater",()=>({DesktopUpdater:()=>null}));
vi.mock("../services/productSupport",async original=>({...await original<object>(),submitFeedback:vi.fn()}));
beforeEach(()=>{vi.clearAllMocks();vi.mocked(submitFeedback).mockResolvedValue("sent");});
it.each([false,true])("setup/workspace ready=%s uses the same service and request schema",async ready=>{
 render(<ProductSupport ready={ready} feedbackRequest={ready?1:0} />);
 if(!ready)await userEvent.click(screen.getByRole("button",{name:"피드백"}));
 await userEvent.type(screen.getByLabelText("제목"),"Synthetic title");await userEvent.type(screen.getByLabelText("내용"),"Synthetic message");await userEvent.click(screen.getByRole("button",{name:"전송 정보 미리보기"}));await userEvent.click(screen.getByRole("button",{name:"전송 / GitHub 작성 화면 열기"}));
 expect(await screen.findByText("고마워요. 피드백이 전송되었습니다.")).toBeTruthy();expect(submitFeedback).toHaveBeenCalledWith({schemaVersion:1,category:"bug",title:"Synthetic title",message:"Synthetic message",platform:"desktop",appVersion:"0.4.0"});
});
