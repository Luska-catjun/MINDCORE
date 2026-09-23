import { useCallback, useEffect, useRef, useState } from "react";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { api, ApiError, isDesktopRuntime, setAuthFailureHandler, storeDesktopSession } from "./api/client";
import {
  type ChatHistoryCache,
  type LocalMessage,
  type PendingChatSend,
  reconcileChatResponse,
  replaceDurableChatHistory,
  updateChatHistory,
} from "./chatHistory";
import type { ChatResponse, ProactiveEvent } from "./types/api";
import { Sidebar, type WorkspaceView } from "./components/Sidebar";
import { ChatWindow } from "./components/ChatWindow";
import { WorkspacePanel } from "./components/WorkspacePanel";
import { LoginScreen } from "./components/LoginScreen";
import { SetupWizard } from "./components/SetupWizard";
import { DesktopUpdater } from "./components/DesktopUpdater";
import { invoke } from "@tauri-apps/api/core";
import "./buildRevision";
import { emitFrontendStartupTiming } from "./startupTiming";
import { chatDebug } from "./chatDebug";
import { proactiveDeliveryMode, proactiveUnreadKey, rememberProactiveEvent, shouldMarkProactiveUnread, shouldSendProactiveOsNotification } from "./proactiveEvents";
import { DEFAULT_PERSONA_DISPLAY_NAME, DEFAULT_USER_DISPLAY_NAME } from "./assets";
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
type RuntimeCapabilities = { updater_available: boolean };

function App() {
  const [personaDisplayName, setPersonaDisplayName] = useState(DEFAULT_PERSONA_DISPLAY_NAME);
  const [userDisplayName, setUserDisplayName] = useState(DEFAULT_USER_DISPLAY_NAME);
  const [activePersonaId, setActivePersonaId] = useState<string | null>(null);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [avatarRevision, setAvatarRevision] = useState(0);
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
  const [updaterAvailable, setUpdaterAvailable] = useState(false);
  const [unreadByConversation, setUnreadByConversation] = useState<Record<string, number>>({});
  const [proactiveToast, setProactiveToast] = useState<ProactiveEvent | null>(null);
  const eventCursorRef = useRef<{ createdAt: string; messageId: string } | null>(null);
  const eventCursorPersonaRef = useRef<string | null>(null);
  const seenProactiveIdsRef = useRef(new Set<string>());
  const proactivePollingRef = useRef(false);
  const toastTimerRef = useRef<number | null>(null);
  const chatHistoryRef = useRef(chatHistory);
  chatHistoryRef.current = chatHistory;

  const clearSessionState = useCallback(() => {
    sessionGenerationRef.current += 1;
    setAuthStatus("unauthenticated");
    setMainConversationId(null);
    setChatHistory({});
    setPendingSends({});
    setSidebarOpen(false);
    setPersonaDisplayName(DEFAULT_PERSONA_DISPLAY_NAME);
    setUserDisplayName(DEFAULT_USER_DISPLAY_NAME);
    setActivePersonaId(null);
  }, []);

  const loadMainConversation = useCallback(async (expectedGeneration: number) => {
    if (isDesktopRuntime()) emitFrontendStartupTiming("conversation_load_start");
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
      if (isDesktopRuntime()) emitFrontendStartupTiming("conversation_load_end");
      if (expectedGeneration === sessionGenerationRef.current) setConversationLoading(false);
    }
  }, [activePersonaId]);

  const refreshPersonas = useCallback(async () => {
    if (!isDesktopRuntime()) return [];
    const result = await invoke<PersonaSummary[]>("list_personas");
    const loaded = Array.isArray(result) ? result : [];
    setPersonas(loaded);
    setAvatarRevision((revision) => revision + 1);
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
    void invoke<RuntimeCapabilities>("get_runtime_capabilities")
      .then((capabilities) => setUpdaterAvailable(capabilities?.updater_available === true))
      .catch(() => setUpdaterAvailable(false));
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
          // Native readiness is authoritative for the managed desktop child:
          // its 204 is served only after the backend lifespan (DB/schema and
          // runtime hydration) has completed. Do not gate it on an additional
          // /health request, which performs another remote DB round trip.
          await invoke("start_mindcore_backend");
          emitFrontendStartupTiming("native_ready");
          if (!cancelled) { setStartupProgress(100); setBackendStatus("connected"); }
          return;
        } catch {
          if (!cancelled) setBackendStatus("error");
          return;
        }
      }
      try {
        await api.health();
        if (!cancelled) { setStartupProgress(100); setBackendStatus("connected"); }
      } catch {
        if (!cancelled) setBackendStatus("error");
      }
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
    if (!isDesktopRuntime() || authStatus !== "authenticated" || backendStatus !== "connected") {
      eventCursorRef.current = null;
      eventCursorPersonaRef.current = null;
      return;
    }
    if (eventCursorPersonaRef.current !== activePersonaId) {
      eventCursorRef.current = null;
      eventCursorPersonaRef.current = activePersonaId;
    }
    let cancelled = false;
    const poll = async () => {
      if (cancelled || proactivePollingRef.current) return;
      proactivePollingRef.current = true;
      try {
        const batch = await api.proactiveEvents(eventCursorRef.current ?? undefined);
        if (cancelled) return;
        // First sync only establishes a cursor: old durable proactive turns
        // must never replay as fresh notifications after restart.
        const wasBaseline = eventCursorRef.current === null;
        if (batch.latest_created_at && batch.latest_message_id) {
          eventCursorRef.current = { createdAt: batch.latest_created_at, messageId: batch.latest_message_id };
        }
        if (wasBaseline) return;
        let backgroundBatch = false;
        for (const event of batch.events) {
          const eventDedupeKey = `${event.persona_id}:${event.message_id}`;
          if (cancelled || !rememberProactiveEvent(seenProactiveIdsRef.current, eventDedupeKey)) continue;
          const visible = document.visibilityState === "visible";
          let focused = visible && document.hasFocus();
          if (visible && isDesktopRuntime()) {
            try { focused = focused && await getCurrentWindow().isFocused(); } catch { /* in-app behavior remains available */ }
          }
          const mode = proactiveDeliveryMode({
            conversationId: mainConversationId,
            eventConversationId: event.conversation_id,
            activeView,
            visible,
            focused,
          });
          if (shouldMarkProactiveUnread(mode)) {
            const unreadKey = proactiveUnreadKey(event.persona_id, event.conversation_id);
            setUnreadByConversation((current) => ({
              ...current,
              [unreadKey]: (current[unreadKey] ?? 0) + 1,
            }));
          }
          setProactiveToast(event);
          if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
          toastTimerRef.current = window.setTimeout(() => setProactiveToast(null), 6500);

          backgroundBatch ||= shouldSendProactiveOsNotification(mode);

          if (event.conversation_id === mainConversationId) {
            const expectedRevision = chatHistoryRef.current[event.conversation_id]?.revision ?? 0;
            void api.listMessages(event.conversation_id, 200, 0, true).then((messages) => {
              if (cancelled) return;
              setChatHistory((current) => replaceDurableChatHistory(
                current, event.conversation_id, messages, expectedRevision,
              ));
            }).catch(() => undefined);
          }
        }
        if (backgroundBatch) {
          try {
            let granted = await isPermissionGranted();
            if (!granted) granted = (await requestPermission()) === "granted";
            if (granted) sendNotification({ title: "MindCore", body: `${personaDisplayName}가 먼저 말을 걸었어요.` });
          } catch {
            // Permission/platform failures are non-fatal; in-app feedback remains available.
          }
        }
      } catch {
        // Polling is opportunistic and must not surface a runtime error.
      } finally {
        proactivePollingRef.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 10_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activePersonaId, activeView, authStatus, backendStatus, mainConversationId, personaDisplayName]);

  useEffect(() => {
    if (activeView !== "chat" || !activePersonaId || !mainConversationId) return;
    const unreadKey = proactiveUnreadKey(activePersonaId, mainConversationId);
    setUnreadByConversation((current) => {
      if (!current[unreadKey]) return current;
      const next = { ...current };
      delete next[unreadKey];
      return next;
    });
  }, [activePersonaId, activeView, mainConversationId]);

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
      setUserDisplayName(session.user_display_name || DEFAULT_USER_DISPLAY_NAME);
      if (isDesktopRuntime()) emitFrontendStartupTiming("authenticated_session");
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
      setUserDisplayName(session.user_display_name || DEFAULT_USER_DISPLAY_NAME);
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

  const openProactiveEvent = useCallback(async (event: ProactiveEvent) => {
    setProactiveToast(null);
    window.localStorage.setItem(`${MAIN_CONVERSATION_STORAGE_KEY}:${event.persona_id}`, event.conversation_id);
    if (event.persona_id !== activePersonaId) await switchPersona(event.persona_id);
    else { setMainConversationId(event.conversation_id); setActiveView("chat"); }
    const unreadKey = proactiveUnreadKey(event.persona_id, event.conversation_id);
    setUnreadByConversation((current) => { const next = { ...current }; delete next[unreadKey]; return next; });
    try {
      const windowHandle = getCurrentWindow();
      await windowHandle.unminimize();
      await windowHandle.show();
      await windowHandle.setFocus();
    } catch { /* already foreground or native focus denied */ }
  }, [activePersonaId, switchPersona]);

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
        unreadCount={Object.values(unreadByConversation).reduce((total, count) => total + count, 0)}
      />

      <main className="main-area">
        <div className="session-toolbar">
          {isDesktopRuntime() && <><label className="persona-selector"><PersonaAvatar personaId={activePersona?.persona_id ?? activePersonaId} displayName={activePersona?.display_name ?? personaDisplayName} avatarExtension={activePersona?.avatar_extension} revision={avatarRevision} className="persona-avatar-small" />Persona<select aria-label="Current Persona" value={activePersonaId ?? ""} onChange={(event) => void switchPersona(event.target.value)}>{personas.map((persona) => <option key={persona.persona_id} value={persona.persona_id}>{persona.display_name}</option>)}</select></label><button type="button" onClick={() => setPersonaManagerMode("add")}>+ Add Persona</button><button type="button" onClick={() => setPersonaManagerMode("manage")}>Manage Personas</button><button type="button" onClick={() => void invoke("open_configuration_folder")}>Open Configuration</button><button type="button" onClick={() => void invoke("open_identity_file")}>Open Identity File</button><button type="button" onClick={() => { void invoke("stop_mindcore_backend").finally(() => { setReconfiguring(true); setSetupState("needed"); }); }}>Reconfigure Active Persona</button></>}
          {!isDesktopRuntime() && <><span>Private access</span><button type="button" onClick={handleLogout}>Log out</button></>}
        </div>
        {backendStatus === "error" && (
          <div className="error-banner error-banner-top">백엔드 서버에 연결할 수 없습니다. FastAPI 서버와 API 주소를 확인해주세요.</div>
        )}
        {globalError && <div className="error-banner error-banner-top">{globalError}</div>}
        {isDesktopRuntime() && updaterAvailable && <DesktopUpdater />}

        {activeView === "chat" ? (
          <ChatWindow
            personaDisplayName={personaDisplayName}
            userDisplayName={userDisplayName}
            personaAvatarExtension={activePersona?.avatar_extension}
            personaAvatarRevision={avatarRevision}
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
      {personaManagerMode && <PersonaManager mode={personaManagerMode} personas={personas} avatarRevision={avatarRevision} onClose={() => setPersonaManagerMode(null)} onChanged={handlePersonasChanged} />}
      {proactiveToast && <button className="proactive-toast" type="button" onClick={() => void openProactiveEvent(proactiveToast)}>{personaDisplayName}가 새 메시지를 보냈어요. 열기</button>}
    </div>
  );
}

export default App;
