//! WRA-01/02: compose the same native helpers used by the Tauri commands.
//! Only external credentials/network use isolated synthetic adapters.
use super::*;
use std::cell::RefCell;

fn draft() -> SetupDraft {
    SetupDraft {
        database_url: "libsql://wra-synthetic.invalid".into(),
        database_auth_token: "wra-test-token".into(),
        llm_provider: "gemini".into(),
        llm_model: "wra-test-model".into(),
        api_key: "wra-test-provider-key".into(),
        provider_models: BTreeMap::new(),
        provider_api_keys: BTreeMap::new(),
        provider_preserve_keys: BTreeMap::new(),
        user_display_name: "Synthetic User".into(),
        persona_display_name: "Synthetic Persona".into(),
        preserve_database_auth_token: false,
        preserve_api_key: false,
        preserve_identity: false,
    }
}

#[derive(Default)]
struct Credentials(RefCell<BTreeMap<String, String>>);
impl Credentials {
    fn call(&self, action: &str, id: &str, value: Option<&str>) -> Result<String, String> {
        match action {
            "get" => self
                .0
                .borrow()
                .get(id)
                .cloned()
                .ok_or("Missing synthetic credential".into()),
            "store" => {
                self.0.borrow_mut().insert(id.into(), value.unwrap().into());
                Ok(String::new())
            }
            "delete" => {
                self.0.borrow_mut().remove(id);
                Ok(String::new())
            }
            _ => panic!("Unexpected credential action"),
        }
    }
}

fn llm_stage(d: &SetupDraft) -> Result<String, String> {
    validate_setup_action("llm", d)?;
    let values = provider_draft_values(d, BTreeMap::new(), true)?;
    preflight_env_for_action("llm", &render_env_values(values))
}

fn fixture(
    action: &str,
    path: &Path,
    token: Option<&str>,
    database: &Path,
    fail: bool,
) -> Result<String, String> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let venv = root.join(".venv/bin/python");
    let python = if venv.is_file() {
        venv
    } else {
        PathBuf::from("python")
    };
    let mut command = std::process::Command::new(python);
    command
        .current_dir(root)
        .arg(root.join("tests/wra_setup_fixture.py"))
        .arg(action)
        .arg(path)
        .arg(database)
        .env_remove("WRA_FIXTURE_TOKEN");
    if let Some(token) = token {
        command.env("WRA_FIXTURE_TOKEN", token);
    }
    if fail {
        command.arg("fail-provider");
    }
    let output = command
        .output()
        .map_err(|_| "Fixture process failed".to_string())?;
    assert!(
        output.status.success(),
        "Synthetic setup process failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let response = String::from_utf8(output.stdout).unwrap().trim().to_string();
    if response == "PROVIDER_MODEL_FAILURE" {
        Err(response)
    } else {
        Ok(response)
    }
}

fn values(
    config: &Path,
    identity: &Path,
    d: &SetupDraft,
    existing: Option<&PersonaRegistry>,
) -> BTreeMap<String, String> {
    let global = fs::read_to_string(config)
        .map(|s| persona_registry::parse_env(&s))
        .unwrap_or_default();
    persona_registry::parse_env(
        &draft_env_values(
            d,
            &d.persona_display_name,
            identity,
            global,
            existing
                .map(persona_registry::active_profile)
                .transpose()
                .unwrap(),
        )
        .unwrap(),
    )
}

fn reload(config: &Path, credentials: &Credentials) -> Result<Option<PersonaRegistry>, String> {
    let registry = persona_registry::load_registry(config)?;
    if let Some(registry) = &registry {
        for profile in &registry.personas {
            migrate_profile_database_credential_with(
                profile,
                |a, id, v| credentials.call(a, id, v),
                persona_registry::secure_atomic_write,
            )?;
        }
    }
    Ok(registry)
}

fn save(
    config: &Path,
    identity: &Path,
    d: &SetupDraft,
    credentials: &Credentials,
) -> Result<(), String> {
    let existing = reload(config, credentials)?;
    let values = values(config, identity, d, existing.as_ref());
    save_mindcore_config_with(
        config,
        identity,
        d,
        "Synthetic identity",
        &values,
        existing,
        |a, id, v| credentials.call(a, id, v),
        persona_registry::secure_atomic_write,
        persona_registry::save_registry,
    )
}

fn eligible(config: &Path, credentials: &Credentials) -> Result<bool, String> {
    config_is_complete_with(
        config,
        || reload(config, credentials),
        |id| credentials.call("get", id, None),
    )
}

#[test]
fn llm_native_preflight_has_no_db_dependency_and_selected_provider_only() {
    let temp = tempfile::tempdir().unwrap();
    let mut d = draft();
    d.database_url.clear();
    d.database_auth_token.clear();
    d.provider_preserve_keys.insert("groq".into(), true);
    let staged = llm_stage(&d).unwrap();
    let parsed = persona_registry::parse_env(&staged);
    assert!(!parsed.contains_key("DATABASE_CREDENTIAL_ID"));
    assert!(!parsed.contains_key("GROQ_API_KEY"));
    assert_eq!(parsed.get("LLM_FALLBACK_PROVIDER").unwrap(), "");
    let path = temp.path().join("stage.env");
    let result = execute_staged_setup_with(
        "llm",
        &d,
        &path,
        &staged,
        |_| panic!("LLM must not lookup DB credentials"),
        |path, token| {
            assert!(token.is_none());
            fixture("llm", path, token, &temp.path().join("db.sqlite"), false)
        },
    );
    assert_eq!(result.unwrap(), "LLM_CONNECTED");
    assert!(!path.exists());
}

#[test]
fn missing_provider_key_remains_fail_closed() {
    let mut d = draft();
    d.api_key.clear();
    assert!(llm_stage(&d).is_err());
    d.preserve_api_key = true;
    assert!(llm_stage(&d).is_err());
}

#[test]
fn genuine_provider_model_failure_is_not_a_database_failure() {
    let temp = tempfile::tempdir().unwrap();
    let d = draft();
    let result = execute_staged_setup_with(
        "llm",
        &d,
        &temp.path().join("stage.env"),
        &llm_stage(&d).unwrap(),
        |_| panic!("No DB dependency"),
        |path, token| fixture("llm", path, token, &temp.path().join("db.sqlite"), true),
    );
    assert_eq!(result.unwrap_err(), "PROVIDER_MODEL_FAILURE");
}

#[test]
fn database_actions_require_reference_and_real_token() {
    let temp = tempfile::tempdir().unwrap();
    let mut d = draft();
    for action in ["database", "classify", "initialize"] {
        assert!(execute_staged_setup_with(
            action,
            &d,
            &temp.path().join("stage.env"),
            "DATABASE_URL=synthetic\n",
            |_| panic!("Missing reference must fail first"),
            |_, _| panic!("Must not execute")
        )
        .is_err());
    }
    d.database_auth_token.clear();
    assert!(execute_staged_setup_with(
        "database",
        &d,
        &temp.path().join("stage.env"),
        "DATABASE_CREDENTIAL_ID=synthetic\n",
        |_| panic!("Missing token must fail first"),
        |_, _| panic!("Must not execute")
    )
    .is_err());
    d.preserve_database_auth_token = true;
    assert!(execute_staged_setup_with(
        "database",
        &d,
        &temp.path().join("stage.env"),
        "DATABASE_CREDENTIAL_ID=synthetic\n",
        |_| Err("Missing synthetic credential".into()),
        |_, _| panic!("Must not execute")
    )
    .is_err());
}

#[test]
fn fresh_native_setup_save_reload_eligibility_and_restart() {
    let temp = tempfile::tempdir().unwrap();
    let d = draft();
    let credentials = Credentials::default();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    assert!(!eligible(&config, &credentials).unwrap());
    let full = values(&config, &identity, &d, None);
    let persona_id = full.get("DATABASE_CREDENTIAL_ID").unwrap().clone();
    let db = temp.path().join("db.sqlite");
    for (action, expected) in [
        ("database", "DATABASE_CONNECTED"),
        ("llm", "LLM_CONNECTED"),
        ("classify", "EMPTY"),
        ("initialize", "BOOTSTRAPPED"),
    ] {
        let staged = if action == "llm" {
            llm_stage(&d).unwrap()
        } else {
            preflight_env_for_action(action, &render_env_values(full.clone())).unwrap()
        };
        assert_eq!(
            execute_staged_setup_with(
                action,
                &d,
                &temp.path().join("stage.env"),
                &staged,
                |_| panic!("Fresh preflight does not store/read OS credentials"),
                |path, token| fixture(action, path, token, &db, false)
            )
            .unwrap(),
            expected
        );
    }
    assert!(credentials.0.borrow().is_empty());
    save_mindcore_config_with(
        &config,
        &identity,
        &d,
        "Synthetic identity",
        &full,
        None,
        |a, id, v| credentials.call(a, id, v),
        persona_registry::secure_atomic_write,
        persona_registry::save_registry,
    )
    .unwrap();
    for _ in 0..2 {
        let registry = reload(&config, &credentials).unwrap().unwrap();
        let profile = persona_registry::active_profile(&registry).unwrap();
        assert_eq!(profile.persona_id, persona_id);
        assert_eq!(
            profile_credential_id(Path::new(&profile.config_path)).unwrap(),
            persona_id
        );
        assert_eq!(
            credentials.call("get", &persona_id, None).unwrap(),
            d.database_auth_token
        );
        assert!(!fs::read_to_string(&profile.config_path)
            .unwrap()
            .contains("DATABASE_AUTH_TOKEN"));
        assert!(!fs::read_to_string(&config)
            .unwrap()
            .contains("DATABASE_AUTH_TOKEN"));
        assert!(eligible(&config, &credentials).unwrap());
    }
    assert_eq!(credentials.0.borrow().len(), 1);
}

#[test]
fn replacement_preflight_save_reload_preserves_identity_and_database() {
    let temp = tempfile::tempdir().unwrap();
    let mut d = draft();
    let credentials = Credentials::default();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    save(&config, &identity, &d, &credentials).unwrap();
    let registry = reload(&config, &credentials).unwrap().unwrap();
    let id = registry.active_persona_id.clone();
    let identity_before = fs::read(&identity).unwrap();
    let db = temp.path().join("db.sqlite");
    let old_values = values(&config, &identity, &d, Some(&registry));
    let staged = preflight_env_for_action("initialize", &render_env_values(old_values)).unwrap();
    execute_staged_setup_with(
        "initialize",
        &d,
        &temp.path().join("stage.env"),
        &staged,
        |_| panic!("Token stays in memory"),
        |p, t| fixture("initialize", p, t, &db, false),
    )
    .unwrap();
    let db_before = fs::read(&db).unwrap();
    d.database_auth_token = "wra-replacement-token".into();
    d.preserve_identity = true;
    let next_values = values(&config, &identity, &d, Some(&registry));
    assert_eq!(next_values.get("DATABASE_CREDENTIAL_ID").unwrap(), &id);
    let staged = preflight_env_for_action("database", &render_env_values(next_values)).unwrap();
    execute_staged_setup_with(
        "database",
        &d,
        &temp.path().join("stage.env"),
        &staged,
        |_| panic!("Replacement preflight must not read old token"),
        |p, t| fixture("database", p, t, &db, false),
    )
    .unwrap();
    assert_eq!(
        credentials.call("get", &id, None).unwrap(),
        "wra-test-token"
    );
    save(&config, &identity, &d, &credentials).unwrap();
    let restarted = reload(&config, &credentials).unwrap().unwrap();
    assert_eq!(restarted.active_persona_id, id);
    assert_eq!(
        credentials.call("get", &id, None).unwrap(),
        "wra-replacement-token"
    );
    assert_eq!(fs::read(&identity).unwrap(), identity_before);
    assert_eq!(fs::read(&db).unwrap(), db_before);
    assert!(eligible(&config, &credentials).unwrap());
    d.preserve_database_auth_token = true;
    d.database_auth_token.clear();
    let staged = preflight_env_for_action(
        "database",
        &render_env_values(values(&config, &identity, &d, Some(&restarted))),
    )
    .unwrap();
    assert_eq!(
        execute_staged_setup_with(
            "database",
            &d,
            &temp.path().join("stage.env"),
            &staged,
            |id| credentials.call("get", id, None),
            |p, t| fixture("database", p, t, &db, false)
        )
        .unwrap(),
        "DATABASE_CONNECTED"
    );
    assert_eq!(credentials.0.borrow().len(), 1);
}

#[test]
fn failed_replacement_preserves_existing_files_and_credential() {
    for failure in ["credential", "profile", "registry", "global"] {
        let temp = tempfile::tempdir().unwrap();
        let mut d = draft();
        let credentials = Credentials::default();
        let config = temp.path().join("mindcore.env");
        let identity = temp.path().join("identity.md");
        save(&config, &identity, &d, &credentials).unwrap();
        let registry = reload(&config, &credentials).unwrap().unwrap();
        let profile = persona_registry::active_profile(&registry).unwrap();
        let paths = [
            config.clone(),
            identity.clone(),
            PathBuf::from(&profile.config_path),
            persona_registry::registry_path(&config).unwrap(),
        ];
        let before: Vec<_> = paths.iter().map(|p| fs::read(p).unwrap()).collect();
        d.database_auth_token = "wra-replacement-token".into();
        let next = values(&config, &identity, &d, Some(&registry));
        let mut failed = false;
        let result = save_mindcore_config_with(
            &config,
            &identity,
            &d,
            "Changed synthetic identity",
            &next,
            Some(registry.clone()),
            |a, id, v| {
                if failure == "credential" && a == "store" {
                    return Err("Injected credential failure".into());
                }
                credentials.call(a, id, v)
            },
            |p, t| {
                if !failed
                    && ((failure == "profile" && p == Path::new(&profile.config_path))
                        || (failure == "global" && p == config))
                {
                    failed = true;
                    return Err("Injected file failure".into());
                }
                persona_registry::secure_atomic_write(p, t)
            },
            |p, r| {
                if failure == "registry" {
                    return Err("Injected registry failure".into());
                }
                persona_registry::save_registry(p, r)
            },
        );
        assert!(result.is_err(), "Must not report false success");
        for (p, bytes) in paths.iter().zip(before) {
            assert_eq!(fs::read(p).unwrap(), bytes);
        }
        assert_eq!(
            credentials.call("get", &profile.persona_id, None).unwrap(),
            "wra-test-token"
        );
        assert!(eligible(&config, &credentials).unwrap());
    }
}

#[test]
fn fresh_save_failure_removes_new_credential_and_configuration() {
    let temp = tempfile::tempdir().unwrap();
    let d = draft();
    let credentials = Credentials::default();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    let next = values(&config, &identity, &d, None);
    assert!(save_mindcore_config_with(
        &config,
        &identity,
        &d,
        "Synthetic identity",
        &next,
        None,
        |a, id, v| credentials.call(a, id, v),
        persona_registry::secure_atomic_write,
        |_, _| Err("Injected registry failure".into())
    )
    .is_err());
    assert!(!config.exists());
    assert!(!identity.exists());
    assert!(persona_registry::load_registry(&config).unwrap().is_none());
    assert!(credentials.0.borrow().is_empty());
}

#[test]
fn incomplete_rollback_returns_explicit_error() {
    let temp = tempfile::tempdir().unwrap();
    let mut d = draft();
    let credentials = Credentials::default();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    save(&config, &identity, &d, &credentials).unwrap();
    let registry = reload(&config, &credentials).unwrap().unwrap();
    d.database_auth_token = "wra-replacement-token".into();
    let next = values(&config, &identity, &d, Some(&registry));
    let result = save_mindcore_config_with(
        &config,
        &identity,
        &d,
        "Synthetic identity",
        &next,
        Some(registry),
        |a, id, v| {
            if a == "store" && v == Some("wra-test-token") {
                return Err("Injected restore failure".into());
            }
            credentials.call(a, id, v)
        },
        persona_registry::secure_atomic_write,
        |_, _| Err("Injected registry failure".into()),
    );
    assert_eq!(
        result.unwrap_err(),
        "Setup save failed and rollback is incomplete."
    );
}

#[test]
fn historical_mismatched_reference_still_rejected() {
    let temp = tempfile::tempdir().unwrap();
    let d = draft();
    let credentials = Credentials::default();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    save(&config, &identity, &d, &credentials).unwrap();
    let registry = reload(&config, &credentials).unwrap().unwrap();
    let profile = persona_registry::active_profile(&registry).unwrap();
    let path = Path::new(&profile.config_path);
    let mut source = persona_registry::parse_env(&fs::read_to_string(path).unwrap());
    source.insert(
        "DATABASE_CREDENTIAL_ID".into(),
        persona_registry::new_persona_id().unwrap(),
    );
    persona_registry::secure_atomic_write(path, &render_env_values(source)).unwrap();
    assert_eq!(
        migrate_profile_database_credential_with(
            profile,
            |_, _, _| panic!("No lookup may bypass identity mismatch"),
            persona_registry::secure_atomic_write
        )
        .unwrap_err(),
        "The Persona database credential reference is invalid."
    );
    assert!(eligible(&config, &credentials).is_err());
}

#[test]
fn initial_registry_rejects_a_different_credential_identity() {
    let temp = tempfile::tempdir().unwrap();
    let d = draft();
    let config = temp.path().join("mindcore.env");
    let identity = temp.path().join("identity.md");
    let source = values(&config, &identity, &d, None);
    assert!(persona_registry::create_initial_registry_with(
        &config,
        &persona_registry::new_persona_id().unwrap(),
        &d.persona_display_name,
        &identity,
        &source,
        |_, _| panic!("Must reject before writing"),
        |_, _| panic!("Must reject before saving")
    )
    .is_err());
}
