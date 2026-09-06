#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum LifecyclePhase {
    Stopped,
    Starting,
    Running,
    Stopping,
    Failed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum StartDecision {
    Spawn { generation: u64 },
    AlreadyRunning,
    InProgress,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum StopDecision {
    Request {
        generation: u64,
        capability: String,
    },
    AlreadyStopped,
    InProgress,
}

struct ActiveChild<C> {
    generation: u64,
    child: C,
    shutdown_capability: String,
}

pub(crate) struct SidecarLifecycle<C> {
    phase: LifecyclePhase,
    next_generation: u64,
    current_generation: Option<u64>,
    active: Option<ActiveChild<C>>,
}

impl<C> SidecarLifecycle<C> {
    pub(crate) fn new() -> Self {
        Self {
            phase: LifecyclePhase::Stopped,
            next_generation: 0,
            current_generation: None,
            active: None,
        }
    }

    pub(crate) fn begin_start(&mut self) -> StartDecision {
        match self.phase {
            LifecyclePhase::Running if self.active.is_some() => {
                return StartDecision::AlreadyRunning;
            }
            LifecyclePhase::Starting | LifecyclePhase::Stopping => {
                return StartDecision::InProgress;
            }
            LifecyclePhase::Stopped | LifecyclePhase::Failed | LifecyclePhase::Running => {}
        }
        self.active = None;
        self.next_generation = self.next_generation.wrapping_add(1).max(1);
        self.current_generation = Some(self.next_generation);
        self.phase = LifecyclePhase::Starting;
        StartDecision::Spawn {
            generation: self.next_generation,
        }
    }

    pub(crate) fn attach_child(
        &mut self,
        generation: u64,
        child: C,
        shutdown_capability: String,
    ) -> Result<(), C> {
        if self.phase != LifecyclePhase::Starting
            || self.current_generation != Some(generation)
            || self.active.is_some()
        {
            return Err(child);
        }
        self.active = Some(ActiveChild {
            generation,
            child,
            shutdown_capability,
        });
        Ok(())
    }

    pub(crate) fn mark_running(&mut self, generation: u64) -> bool {
        if self.phase == LifecyclePhase::Starting
            && self.current_generation == Some(generation)
            && self
                .active
                .as_ref()
                .is_some_and(|active| active.generation == generation)
        {
            self.phase = LifecyclePhase::Running;
            return true;
        }
        false
    }

    pub(crate) fn is_generation_active(&self, generation: u64) -> bool {
        self.current_generation == Some(generation)
            && matches!(
                self.phase,
                LifecyclePhase::Starting | LifecyclePhase::Running | LifecyclePhase::Stopping
            )
    }

    pub(crate) fn is_inactive(&self) -> bool {
        self.active.is_none()
            && matches!(self.phase, LifecyclePhase::Stopped | LifecyclePhase::Failed)
    }

    pub(crate) fn record_exit(&mut self, generation: u64, event_is_error: bool) -> Option<C> {
        if self.current_generation != Some(generation) {
            return None;
        }
        let was_stopping = self.phase == LifecyclePhase::Stopping;
        let child = self.active.take().map(|active| active.child);
        self.current_generation = None;
        self.phase = if was_stopping && !event_is_error {
            LifecyclePhase::Stopped
        } else {
            LifecyclePhase::Failed
        };
        child
    }

    pub(crate) fn fail_start(&mut self, generation: u64) -> Option<C> {
        if self.current_generation != Some(generation) {
            return None;
        }
        let child = self.active.take().map(|active| active.child);
        self.current_generation = None;
        self.phase = LifecyclePhase::Failed;
        child
    }

    pub(crate) fn begin_stop(&mut self) -> StopDecision {
        if self.phase == LifecyclePhase::Stopping {
            return StopDecision::InProgress;
        }
        let Some((generation, capability)) = self
            .active
            .as_ref()
            .map(|active| (active.generation, active.shutdown_capability.clone()))
        else {
            self.phase = LifecyclePhase::Stopped;
            self.current_generation = None;
            return StopDecision::AlreadyStopped;
        };
        self.phase = LifecyclePhase::Stopping;
        StopDecision::Request {
            generation,
            capability,
        }
    }

    pub(crate) fn finish_stop(&mut self, generation: u64) -> Option<C> {
        if self.current_generation != Some(generation) {
            return None;
        }
        let child = self.active.take().map(|active| active.child);
        self.current_generation = None;
        self.phase = LifecyclePhase::Stopped;
        child
    }

    #[cfg(test)]
    fn phase(&self) -> LifecyclePhase {
        self.phase
    }

    #[cfg(test)]
    fn generation(&self) -> Option<u64> {
        self.current_generation
    }

    #[cfg(test)]
    fn has_child(&self) -> bool {
        self.active.is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::thread;

    fn start_running(state: &mut SidecarLifecycle<&'static str>) -> u64 {
        let StartDecision::Spawn { generation } = state.begin_start() else {
            panic!("expected a new generation");
        };
        state
            .attach_child(generation, "child", format!("capability-{generation}"))
            .expect("child should attach");
        assert!(state.mark_running(generation));
        generation
    }

    #[test]
    fn spawn_success_transitions_to_running() {
        let mut state = SidecarLifecycle::new();
        let generation = start_running(&mut state);
        assert_eq!(state.phase(), LifecyclePhase::Running);
        assert_eq!(state.generation(), Some(generation));
        assert!(state.has_child());
        assert_eq!(state.begin_start(), StartDecision::AlreadyRunning);
    }

    #[test]
    fn immediate_exit_clears_the_handle_and_allows_restart() {
        let mut state = SidecarLifecycle::new();
        let StartDecision::Spawn { generation } = state.begin_start() else {
            panic!("expected spawn");
        };
        state
            .attach_child(generation, "first", "first-capability".into())
            .expect("child should attach");
        assert_eq!(state.record_exit(generation, false), Some("first"));
        assert_eq!(state.phase(), LifecyclePhase::Failed);
        assert!(!state.has_child());
        let StartDecision::Spawn {
            generation: replacement,
        } = state.begin_start()
        else {
            panic!("failed child must be restartable");
        };
        assert!(replacement > generation);
    }

    #[test]
    fn crash_after_running_clears_the_active_child() {
        let mut state = SidecarLifecycle::new();
        let generation = start_running(&mut state);
        assert_eq!(state.record_exit(generation, true), Some("child"));
        assert_eq!(state.phase(), LifecyclePhase::Failed);
        assert_eq!(state.generation(), None);
        assert!(!state.has_child());
    }

    #[test]
    fn stop_then_start_uses_a_new_generation_and_capability() {
        let mut state = SidecarLifecycle::new();
        let first = start_running(&mut state);
        assert_eq!(
            state.begin_stop(),
            StopDecision::Request {
                generation: first,
                capability: format!("capability-{first}"),
            }
        );
        assert_eq!(state.record_exit(first, false), Some("child"));
        assert_eq!(state.phase(), LifecyclePhase::Stopped);
        let second = start_running(&mut state);
        assert!(second > first);
    }

    #[test]
    fn concurrent_start_allows_only_one_spawn_decision() {
        let state = Arc::new(Mutex::new(SidecarLifecycle::<()>::new()));
        let decisions: Vec<_> = (0..8)
            .map(|_| {
                let state = Arc::clone(&state);
                thread::spawn(move || state.lock().expect("lifecycle lock").begin_start())
            })
            .map(|thread| thread.join().expect("start thread"))
            .collect();
        assert_eq!(
            decisions
                .iter()
                .filter(|decision| matches!(decision, StartDecision::Spawn { .. }))
                .count(),
            1
        );
        assert_eq!(
            decisions
                .iter()
                .filter(|decision| **decision == StartDecision::InProgress)
                .count(),
            7
        );
    }

    #[test]
    fn stale_generation_exit_cannot_clear_replacement_child() {
        let mut state = SidecarLifecycle::new();
        let first = start_running(&mut state);
        assert_eq!(state.record_exit(first, true), Some("child"));
        let second = start_running(&mut state);
        assert_eq!(state.record_exit(first, false), None);
        assert_eq!(state.phase(), LifecyclePhase::Running);
        assert_eq!(state.generation(), Some(second));
        assert!(state.has_child());
    }

    #[test]
    fn readiness_failure_marks_generation_failed_for_recovery() {
        let mut state = SidecarLifecycle::new();
        let StartDecision::Spawn { generation } = state.begin_start() else {
            panic!("expected spawn");
        };
        state
            .attach_child(generation, "conflicted", "private-capability".into())
            .expect("child should attach");
        assert_eq!(state.fail_start(generation), Some("conflicted"));
        assert_eq!(state.phase(), LifecyclePhase::Failed);
        assert!(!state.has_child());
        assert!(matches!(state.begin_start(), StartDecision::Spawn { .. }));
    }

    #[test]
    fn stop_during_spawn_cancels_late_child_attachment() {
        let mut state = SidecarLifecycle::new();
        let StartDecision::Spawn { generation } = state.begin_start() else {
            panic!("expected spawn");
        };
        assert_eq!(state.begin_stop(), StopDecision::AlreadyStopped);
        assert_eq!(
            state.attach_child(generation, "late-child", "late-capability".into()),
            Err("late-child")
        );
        assert_eq!(state.phase(), LifecyclePhase::Stopped);
        assert!(!state.has_child());
    }
}
