import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, isDesktopRuntime, setAuthFailureHandler, storeDesktopSession } from "./api/client";
import {
  type ChatHistoryCache,
  type LocalMessage,
  type PendingChatSend,
  reconcileChatResponse,
  replaceDurableChatHistory,
  updateChatHistory,
} from "./chatHistory";
import type { ChatResponse } from "./types/api";
import { Sidebar, type WorkspaceView } from "./components/Sidebar";
import { ChatWindow } from "./components/ChatWindow";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { LoginScreen } from "./components/LoginScreen";
import { SetupWizard } from "./components/SetupWizard";
import { DesktopUpdater } from "./components/DesktopUpdater";
import { invoke } from "@tauri-apps/api/core";
import "./buildRevision";
import { chatDebug } from "./chatDebug";
import { DEFAULT_PERSONA_DISPLAY_NAME } from "./assets";
import { PersonaManager, type PersonaSummary } from "./components/PersonaManager";
import { PersonaAvatar } from "./components/PersonaAvatar";
import "./styles.css";

const SOURCE_DEVICE = "web";
const LEGACY_MAIN_CONVERSATION_STORAGE_KEY = "diana-main-conversation-id";
const MAIN_CONVERSATION_STORAGE_KEY = "mindcore-main-conversation-id";
const DEFAULT_CONVERSATION_TITLE = "MindCore conversation";

type BackendStatus = "checking" | "connected" | "error";
type AuthStatus = "checking" | "authenticated" | "unauthenticated";
type SetupState = "checking" | "needed" | "configured";

function App() {
  const [personaDisplayName, setPersonaDisplayName] = useState(DEFAULT_PERSONA_DISPLAY_NAME);
  const [activePersonaId, setActivePersonaId] = useState<string | null>(null);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [personaManagerMode, setPersonaManagerMode] = useState<"add" | "manage" | null>(null);
  const [mainConversationId, setMainConversationId] = useState<string | null>(null);
  // Chat is conditionally unmounted while an Observation view is open. Keep
  // its per-conversation history above that view boundary so returning to Chat
  // never looks like durable messages were deleted.
  const [chatHistory, setChatHistory] = useState<ChatHistoryCache>({});
  const [pendingSends, setPendingSends] = useState<Record<string, PendingChatSend>>({});
  const sessionGenerationRef = useRef(0);
  const [conversationLoading, setConversationLoading] = useState(true);
  const [activeView, setActiveView] = useState<WorkspaceView>("chat");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [desktopSessionError, setDesktopSessionError] = useState(false);
  const [startupAttempt, setStartupAttempt] = useState(0);
  const [startupProgress, setStartupProgress] = useState(8);
  const [setupState, setSetupState] = useState<SetupState>(isDesktopRuntime() ? "checking" : "configured");
  const [reconfiguring, setReconfiguring] = useState(false);

  const clearSessionState = useCallback(() => {
    sessionGenerationRef.current += 1;
    setAuthStatus("unauthenticated");
    setMainConversationId(null);
    setChatHistory({});
    setPendingSends({});
    setSidebarOpen(false);
    setPersonaDisplayName(DEFAULT_PERSONA_DISPLAY_NAME);
    setActivePersonaId(null);
  }, []);

  const loadMainConversation = useCallback(async (expectedGeneration: number) => {
    setConversationLoading(true);
    try {
      const conversations = await api.listConversations(200);
      if (expectedGeneration !== sessionGenerationRef.current) return;
      const storageKey = `${MAIN_CONVERSATION_STORAGE_KEY}:${activePersonaId ?? "web"}`;
      const storedId = window.localStorage.getItem(storageKey)
        ?? window.localStorage.getItem(LEGACY_MAIN_CONVERSATION_STORAGE_KEY);
      const storedConversation = storedId
        ? conversations.find((conversation) => conversation.id === storedId)
        : undefined;
      const conversation = storedConversation ?? conversations[0] ?? await api.createConversation({
        title: DEFAULT_CONVERSATION_TITLE,
        source_device: SOURCE_DEVICE,
      });

      if (expectedGeneration !== sessionGenerationRef.current) return;
      window.localStorage.setItem(storageKey, conversation.id);
      setMainConversationId(conversation.id);
    } catch (error) {
      if (expectedGeneration !== sessionGenerationRef.current) return;
      setGlobalError(
        error instanceof ApiError
          ? error.message
          : "MindCore 대화를 준비하는 중 오류가 발생했습니다."
      );
    } finally {
      if (expectedGeneration === sessionGenerationRef.current) setConversationLoading(false);
    }
  }, [activePersonaId]);

  const refreshPersonas = useCallback(async () => {
    if (!isDesktopRuntime()) return [];
    const result = await invoke<PersonaSummary[]>("list_personas");
    const loaded = Array.isArray(result) ? result : [];
    setPersonas(loaded);
    const active = loaded.find((persona) => persona.active);
    if (active) {
      setActivePersonaId(active.persona_id);
      setPersonaDisplayName(active.display_name);
    }
    return loaded;
  }, []);

  useEffect(() => {
    if (!isDesktopRuntime()) return;
    void invoke<{ configured: boolean }>("get_setup_status").then((status) => setSetupState(status.configured ? "configured" : "needed")).catch(() => setSetupState("needed"));
  }, []);

  useEffect(() => {
    if (setupState === "configured" && isDesktopRuntime()) void refreshPersonas().catch(() => setGlobalError("Could not load Personas."));
  }, [refreshPersonas, setupState]);

  useEffect(() => {
    if (setupState !== "configured") return;
    let cancelled = false;
    const checkHealth = async () => {
      setBackendStatus("checking");
      setStartupProgress(18);
      if (isDesktopRuntime()) {
        try {
          // Native start is idempotent for a healthy managed child and creates
          // a new generation after a crash or completed stop.
          await invoke("start_mindcore_backend");
        } catch {
          if (!cancelled) setBackendStatus("error");
          return;
        }
      }
      // The packaged sidecar normally starts in well under a second. Polling
      // avoids an authentication/API storm while it is still coming up.
      const deadline = Date.now() + (isDesktopRuntime() ? 30_000 : 0);
      do {
        try {
          await api.health();
          if (!cancelled) { setStartupProgress(100); setBackendStatus("connected"); }
          return;
        } catch {
          if (!isDesktopRuntime() || Date.now() >= deadline) {
            if (!cancelled) setBackendStatus("error");
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 250));
        }
      } while (!cancelled);
    };
    void checkHealth();
    const progressTimer = window.setInterval(() => setStartupProgress((value) => value < 90 ? Math.min(90, value + 2) : value), 700);
    setAuthFailureHandler(clearSessionState);
    return () => {
      cancelled = true;
      window.clearInterval(progressTimer);
      setAuthFailureHandler(undefined);
    };
  }, [clearSessionState, setupState, startupAttempt]);

  useEffect(() => {
    if (backendStatus !== "connected") return;
    const expectedGeneration = sessionGenerationRef.current;
    setDesktopSessionError(false);
    const authenticate = isDesktopRuntime()
      ? invoke<string>("get_desktop_session").then((token) => { storeDesktopSession(token); return api.me(); })
      : api.me();
    authenticate.then((session) => {
      if (expectedGeneration !== sessionGenerationRef.current) return;
      setActivePersonaId(session.persona_id || null);
      setPersonaDisplayName(session.persona_display_name || DEFAULT_PERSONA_DISPLAY_NAME);
      setAuthStatus("authenticated");
    }).catch((error) => {
      if (expectedGeneration !== sessionGenerationRef.current) return;
      if (isDesktopRuntime()) {
        setDesktopSessionError(true);
        setAuthStatus("unauthenticated");
        return;
      }
      if (error instanceof ApiError && error.status !== 401 && error.status !== 403) {
        setLoginError(error.message);
      }
      setAuthStatus("unauthenticated");
    });
  }, [backendStatus, startupAttempt]);

  useEffect(() => {
    if (authStatus === "authenticated") {
      void loadMainConversation(sessionGenerationRef.current);
    }
  }, [authStatus, loadMainConversation]);

  const handleLogin = async (password: string) => {
    setLoginError(null);
    try {
      const session = await api.login(password);
      setActivePersonaId(session.persona_id || null);
      setPersonaDisplayName(session.persona_display_name || DEFAULT_PERSONA_DISPLAY_NAME);
      setAuthStatus("authenticated");
    } catch (error) {
      setLoginError(error instanceof ApiError ? error.message : "Sign in failed.");
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } finally {
      clearSessionState();
    }
  };

  const retryDesktopBackend = useCallback(() => {
    setBackendStatus("checking");
    setAuthStatus("checking");
    setDesktopSessionError(false);
    setStartupAttempt((value) => value + 1);
  }, []);

  const handleViewChange = (view: WorkspaceView) => {
    setActiveView(view);
  };

  const switchPersona = useCallback(async (personaId: string) => {
    if (!isDesktopRuntime() || personaId === activePersonaId) return;
    setBackendStatus("checking");
    setAuthStatus("checking");
    setGlobalError(null);
    sessionGenerationRef.current += 1;
    setMainConversationId(null);
    setChatHistory({});
    setPendingSends({});
    setActiveView("chat");
    try {
      const active = await invoke<PersonaSummary>("switch_active_persona", { personaId });
      setActivePersonaId(active.persona_id);
      setPersonaDisplayName(active.display_name);
    } catch {
      setGlobalError("Persona switch failed. The previous Persona remains active.");
    } finally {
      setStartupAttempt((value) => value + 1);
    }
  }, [activePersonaId]);

  const handlePersonasChanged = useCallback(async (switchTo?: string) => {
    const loaded = await refreshPersonas();
    if (switchTo && switchTo !== activePersonaId) {
      await switchPersona(switchTo);
    } else if (switchTo === activePersonaId) {
      setBackendStatus("checking");
      setAuthStatus("checking");
      sessionGenerationRef.current += 1;
      setMainConversationId(null);
      setChatHistory({});
      setPendingSends({});
      setStartupAttempt((value) => value + 1);
    } else {
      setPersonas(loaded);
    }
  }, [activePersonaId, refreshPersonas, switchPersona]);

  const handleMessagesChange = useCallback(
    (conversationId: string, updater: (messages: LocalMessage[]) => LocalMessage[], event = "app_cache") => {
      setChatHistory((current) => {
        const next = updateChatHistory(current, conversationId, updater);
        const entry = next[conversationId];
        chatDebug(event, { conversationId, messages: entry.messages, revision: entry.revision });
        return next;
      });
    },
    [],
  );

  const handleDurableMessagesLoaded = useCallback(
    (conversationId: string, messages: LocalMessage[], expectedRevision: number) => {
      setChatHistory((current) => {
        const currentEntry = current[conversationId] ?? { messages: [], revision: 0 };
        const applied = currentEntry.revision === expectedRevision;
        chatDebug(applied ? "fetch_applied" : "fetch_discarded", {
          conversationId,
          requestRevision: expectedRevision,
          currentRevision: currentEntry.revision,
        });
        const next = replaceDurableChatHistory(current, conversationId, messages, expectedRevision);
        const entry = next[conversationId];
        chatDebug("app_cache", { conversationId, messages: entry.messages, revision: entry.revision });
        return next;
      });
    },
    [],
  );

  const settlePendingSend = useCallback((send: PendingChatSend) => {
    setPendingSends((current) => {
      if (current[send.conversationId]?.tempId !== send.tempId) return current;
      const next = { ...current };
      delete next[send.conversationId];
      return next;
    });
  }, []);

  const handleSendStarted = useCallback(
    (conversationId: string, optimistic: LocalMessage): PendingChatSend => {
      const send = {
        conversationId,
        tempId: optimistic.id,
        sessionGeneration: sessionGenerationRef.current,
      };
      setPendingSends((current) => ({ ...current, [conversationId]: send }));
      handleMessagesChange(conversationId, (previous) => [...previous, optimistic], "optimistic_user_added");
      return send;
    },
    [handleMessagesChange],
  );

  const handleSendSucceeded = useCallback(
    (send: PendingChatSend, reply: ChatResponse): boolean => {
      if (send.sessionGeneration !== sessionGenerationRef.current) {
        chatDebug("stale_send_discarded", { conversationId: send.conversationId });
        return false;
      }
      if (
        reply.user_message.conversation_id !== send.conversationId
        || reply.diana_message.conversation_id !== send.conversationId
      ) {
        chatDebug("cross_conversation_send_discarded", { conversationId: send.conversationId });
        settlePendingSend(send);
        return false;
      }
      handleMessagesChange(
        send.conversationId,
        (previous) => reconcileChatResponse(
          previous,
          send.conversationId,
          send.tempId,
          [reply.user_message, reply.diana_message],
        ),
        "durable_user_assistant_merged",
      );
      settlePendingSend(send);
      return true;
    },
    [handleMessagesChange, settlePendingSend],
  );

  const handleSendFailed = useCallback(
    (send: PendingChatSend): boolean => {
      if (send.sessionGeneration !== sessionGenerationRef.current) {
        chatDebug("stale_send_discarded", { conversationId: send.conversationId });
        return false;
      }
      handleMessagesChange(
        send.conversationId,
        (previous) => previous.map((message) => (
          message.id === send.tempId
            ? { ...message, _pending: false, _failed: true }
            : message
        )),
        "send_failed",
      );
      settlePendingSend(send);
      return true;
    },
    [handleMessagesChange, settlePendingSend],
  );

  const handleMessageDeleted = useCallback(
    (conversationId: string, messageId: string) => {
      handleMessagesChange(
        conversationId,
        (messages) => messages.filter((message) => message.id !== messageId),
        "message_deleted",
      );
    },
    [handleMessagesChange],
  );

  const currentHistory = mainConversationId ? chatHistory[mainConversationId] : undefined;
  const activePersona = personas.find((persona) => persona.persona_id === activePersonaId) ?? null;

  useEffect(() => {
    if (!mainConversationId) return;
    chatDebug("app_cache", {
      conversationId: mainConversationId,
      messages: currentHistory?.messages ?? [],
      revision: currentHistory?.revision ?? 0,
    });
  }, [currentHistory, mainConversationId]);

  if (isDesktopRuntime() && setupState === "checking") {
    return <main className="login-screen"><div className="login-form"><div className="login-title">MINDCORE</div><p>Preparing local setup…</p></div></main>;
  }
  if (isDesktopRuntime() && setupState === "needed") {
    return <SetupWizard reconfigure={reconfiguring} onComplete={() => { setReconfiguring(false); setSetupState("configured"); setStartupAttempt((value) => value + 1); }} />;
  }
  if (isDesktopRuntime() && backendStatus === "checking") {
    return <main className="login-screen"><div className="login-form"><div className="login-title">MINDCORE</div><div className="startup-progress"><span style={{ width: `${startupProgress}%` }} /></div><p>{startupProgress < 25 ? "Preparing local runtime…" : startupProgress < 90 ? "Connecting…" : "Almost ready…"}</p></div></main>;
  }

  if (isDesktopRuntime() && backendStatus === "error") {
    return <main className="login-screen"><div className="login-form"><div className="login-title">MINDCORE</div><p>MindCore could not start.</p><button className="login-button" type="button" onClick={retryDesktopBackend}>Retry</button><button className="login-button" type="button" onClick={() => void invoke("open_configuration_folder")}>Open Configuration</button><p className="workspace-muted">Check the desktop backend diagnostics in the app log.</p></div></main>;
  }

  if (isDesktopRuntime() && (desktopSessionError || authStatus === "unauthenticated")) {
    return <main className="login-screen"><div className="login-form"><div className="login-title">MINDCORE</div><p>MindCore could not start the local session.</p><button className="login-button" type="button" onClick={retryDesktopBackend}>Restart MindCore</button></div></main>;
  }

  if (authStatus !== "authenticated") {
    return <LoginScreen onLogin={handleLogin} error={loginError} />;
  }

  return (
    <div className="app-shell">
      <Sidebar
        activeView={activeView}
        onViewChange={handleViewChange}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        backendStatus={backendStatus}
      />

      <main className="main-area">
        <div className="session-toolbar">
          {isDesktopRuntime() && <><label className="persona-selector"><PersonaAvatar personaId={activePersona?.persona_id ?? activePersonaId} displayName={activePersona?.display_name ?? personaDisplayName} avatarExtension={activePersona?.avatar_extension} className="persona-avatar persona-avatar-small" />Persona<select aria-label="Current Persona" value={activePersonaId ?? ""} onChange={(event) => void switchPersona(event.target.value)}>{personas.map((persona) => <option key={persona.persona_id} value={persona.persona_id}>{persona.display_name}</option>)}</select></label><button type="button" onClick={() => setPersonaManagerMode("add")}>+ Add Persona</button><button type="button" onClick={() => setPersonaManagerMode("manage")}>Manage Personas</button><button type="button" onClick={() => void invoke("open_configuration_folder")}>Open Configuration</button><button type="button" onClick={() => void invoke("open_identity_file")}>Open Identity File</button><button type="button" onClick={() => { void invoke("stop_mindcore_backend").finally(() => { setReconfiguring(true); setSetupState("needed"); }); }}>Reconfigure Active Persona</button></>}
          {!isDesktopRuntime() && <><span>Private access</span><button type="button" onClick={handleLogout}>Log out</button></>}
        </div>
        {backendStatus === "error" && (
          <div className="error-banner error-banner-top">백엔드 서버에 연결할 수 없습니다. FastAPI 서버와 API 주소를 확인해주세요.</div>
        )}
        {globalError && <div className="error-banner error-banner-top">{globalError}</div>}
        {isDesktopRuntime() && <DesktopUpdater />}

        {activeView === "chat" ? (
          <ChatWindow
            personaDisplayName={personaDisplayName}
            personaAvatarExtension={activePersona?.avatar_extension}
            personaId={activePersonaId}
            conversationId={mainConversationId}
            loadingConversation={conversationLoading}
            onToggleSidebar={() => setSidebarOpen((open) => !open)}
            sourceDevice={SOURCE_DEVICE}
            onStateUpdated={() => undefined}
            messages={currentHistory?.messages ?? []}
            historyRevision={currentHistory?.revision ?? 0}
            sending={Boolean(mainConversationId && pendingSends[mainConversationId])}
            onDurableMessagesLoaded={handleDurableMessagesLoaded}
            onSendStarted={handleSendStarted}
            onSendSucceeded={handleSendSucceeded}
            onSendFailed={handleSendFailed}
          />
        ) : (
          <WorkspacePanel
            key={activePersonaId ?? "web"}
            view={activeView}
            backendStatus={backendStatus}
            onToggleSidebar={() => setSidebarOpen((open) => !open)}
            onMessageDeleted={handleMessageDeleted}
          />
        )}
      </main>
      {personaManagerMode && <PersonaManager mode={personaManagerMode} personas={personas} onClose={() => setPersonaManagerMode(null)} onChanged={handlePersonasChanged} />}
    </div>
  );
}

export default App;
