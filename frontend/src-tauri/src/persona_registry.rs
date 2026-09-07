use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const REGISTRY_VERSION: u32 = 1;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PersonaProfile {
    pub persona_id: String,
    pub display_name: String,
    pub identity_path: String,
    pub config_path: String,
    pub created_at: u64,
    pub last_used_at: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PersonaRegistry {
    pub version: u32,
    pub active_persona_id: String,
    pub personas: Vec<PersonaProfile>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PersonaSummary {
    pub persona_id: String,
    pub display_name: String,
    pub created_at: u64,
    pub last_used_at: Option<u64>,
    pub active: bool,
}

pub fn now_epoch_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn new_persona_id() -> Result<String, String> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| "Could not create a Persona identifier.".to_string())?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    ))
}

pub fn registry_path(global_config: &Path) -> Result<PathBuf, String> {
    Ok(global_config
        .parent()
        .ok_or_else(|| "MindCore configuration directory is unavailable.".to_string())?
        .join("personas.json"))
}

fn validate_persona_id(persona_id: &str) -> Result<(), String> {
    if persona_id.is_empty()
        || persona_id.len() > 64
        || !persona_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-')
    {
        return Err("The Persona identifier is invalid.".to_string());
    }
    Ok(())
}

pub fn persona_directory(global_config: &Path, persona_id: &str) -> Result<PathBuf, String> {
    validate_persona_id(persona_id)?;
    Ok(global_config
        .parent()
        .ok_or_else(|| "MindCore configuration directory is unavailable.".to_string())?
        .join("personas")
        .join(persona_id))
}

pub fn validate_display_name(name: &str) -> Result<String, String> {
    let normalized = name.trim();
    if normalized.is_empty()
        || normalized.chars().count() > 80
        || normalized.chars().any(char::is_control)
    {
        return Err("Choose a Persona name of up to 80 characters.".to_string());
    }
    Ok(normalized.to_string())
}

pub fn parse_env(text: &str) -> BTreeMap<String, String> {
    text.lines()
        .filter_map(|line| {
            let (key, raw) = line.split_once('=')?;
            let key = key.trim();
            if key.is_empty() || key.starts_with('#') {
                return None;
            }
            let value = raw.trim();
            let value = value
                .strip_prefix('"')
                .and_then(|item| item.strip_suffix('"'))
                .unwrap_or(value)
                .replace("\\\"", "\"")
                .replace("\\\\", "\\");
            Some((key.to_string(), value))
        })
        .collect()
}

fn env_value(value: &str) -> String {
    format!(
        "\"{}\"",
        value
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('\n', "")
    )
}

pub fn profile_config_text(
    persona_id: &str,
    display_name: &str,
    identity_path: &Path,
    source: &BTreeMap<String, String>,
) -> String {
    let mut lines = vec![
        "DATABASE_BACKEND=turso".to_string(),
        format!("PERSONA_ID={}", env_value(persona_id)),
        format!("PERSONA_DISPLAY_NAME={}", env_value(display_name)),
        format!(
            "PERSONA_IDENTITY_PATH={}",
            env_value(&identity_path.to_string_lossy())
        ),
    ];
    for key in [
        "DATABASE_URL",
        "DATABASE_AUTH_TOKEN",
        "OLD_DATABASE_URL",
        "TURSO_DB_URL",
        "SUPABASE_DB_URL",
    ] {
        if let Some(value) = source.get(key) {
            lines.push(format!("{key}={}", env_value(value)));
        }
    }
    lines.join("\n") + "\n"
}

pub fn secure_atomic_write(path: &Path, text: &str) -> Result<(), String> {
    let directory = path
        .parent()
        .ok_or_else(|| "Configuration directory is unavailable.".to_string())?;
    fs::create_dir_all(directory)
        .map_err(|_| "Could not create MindCore configuration directory.".to_string())?;
    let mut temporary = tempfile::NamedTempFile::new_in(directory)
        .map_err(|_| "Could not create temporary MindCore configuration.".to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| "Could not protect MindCore configuration.".to_string())?;
    }
    temporary
        .write_all(text.as_bytes())
        .and_then(|_| temporary.flush())
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|_| "Could not write MindCore configuration.".to_string())?;
    temporary
        .persist(path)
        .map(|_| ())
        .map_err(|_| "Could not finalize MindCore configuration.".to_string())
}

pub fn load_registry(global_config: &Path) -> Result<Option<PersonaRegistry>, String> {
    let path = registry_path(global_config)?;
    if !path.is_file() {
        return Ok(None);
    }
    let registry: PersonaRegistry = serde_json::from_str(
        &fs::read_to_string(path)
            .map_err(|_| "Could not read the Persona registry.".to_string())?,
    )
    .map_err(|_| "The Persona registry is invalid.".to_string())?;
    validate_registry(&registry)?;
    Ok(Some(registry))
}

pub fn save_registry(global_config: &Path, registry: &PersonaRegistry) -> Result<(), String> {
    validate_registry(registry)?;
    let encoded = serde_json::to_string_pretty(registry)
        .map_err(|_| "Could not serialize the Persona registry.".to_string())?;
    secure_atomic_write(&registry_path(global_config)?, &(encoded + "\n"))
}

pub fn validate_registry(registry: &PersonaRegistry) -> Result<(), String> {
    if registry.version != REGISTRY_VERSION || registry.personas.is_empty() {
        return Err("The Persona registry is unsupported or empty.".to_string());
    }
    let mut ids = std::collections::BTreeSet::new();
    let mut names = std::collections::BTreeSet::new();
    for persona in &registry.personas {
        validate_display_name(&persona.display_name)?;
        validate_persona_id(&persona.persona_id)?;
        if !ids.insert(persona.persona_id.clone())
            || !names.insert(persona.display_name.to_lowercase())
        {
            return Err("Persona identifiers and names must be unique.".to_string());
        }
    }
    if !ids.contains(&registry.active_persona_id) {
        return Err("The active Persona is missing from the registry.".to_string());
    }
    Ok(())
}

pub fn migrate_legacy_config(global_config: &Path) -> Result<Option<PersonaRegistry>, String> {
    if let Some(registry) = load_registry(global_config)? {
        return Ok(Some(registry));
    }
    if !global_config.is_file() {
        return Ok(None);
    }
    let source = parse_env(
        &fs::read_to_string(global_config)
            .map_err(|_| "Could not read the existing MindCore configuration.".to_string())?,
    );
    let Some(database_url) = source.get("DATABASE_URL").filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let Some(identity) = source
        .get("PERSONA_IDENTITY_PATH")
        .filter(|value| Path::new(value).is_file())
    else {
        return Ok(None);
    };
    if database_url.is_empty() {
        return Ok(None);
    }
    let name = validate_display_name(
        source
            .get("PERSONA_DISPLAY_NAME")
            .map(String::as_str)
            .unwrap_or("Persona"),
    )?;
    let persona_id = new_persona_id()?;
    let directory = persona_directory(global_config, &persona_id)?;
    fs::create_dir_all(&directory)
        .map_err(|_| "Could not create the migrated Persona profile.".to_string())?;
    let config_path = directory.join("persona.env");
    secure_atomic_write(
        &config_path,
        &profile_config_text(&persona_id, &name, Path::new(identity), &source),
    )?;
    let created_at = now_epoch_seconds();
    let registry = PersonaRegistry {
        version: REGISTRY_VERSION,
        active_persona_id: persona_id.clone(),
        personas: vec![PersonaProfile {
            persona_id,
            display_name: name,
            identity_path: identity.clone(),
            config_path: config_path.to_string_lossy().into_owned(),
            created_at,
            last_used_at: Some(created_at),
        }],
    };
    save_registry(global_config, &registry)?;
    Ok(Some(registry))
}

pub fn active_profile(registry: &PersonaRegistry) -> Result<&PersonaProfile, String> {
    registry
        .personas
        .iter()
        .find(|persona| persona.persona_id == registry.active_persona_id)
        .ok_or_else(|| "The active Persona profile is unavailable.".to_string())
}

pub fn summaries(registry: &PersonaRegistry) -> Vec<PersonaSummary> {
    registry
        .personas
        .iter()
        .map(|persona| PersonaSummary {
            persona_id: persona.persona_id.clone(),
            display_name: persona.display_name.clone(),
            created_at: persona.created_at,
            last_used_at: persona.last_used_at,
            active: persona.persona_id == registry.active_persona_id,
        })
        .collect()
}

pub fn rename_persona(
    registry: &mut PersonaRegistry,
    persona_id: &str,
    display_name: &str,
) -> Result<PersonaSummary, String> {
    let name = validate_display_name(display_name)?;
    if registry.personas.iter().any(|persona| {
        persona.persona_id != persona_id
            && persona.display_name.to_lowercase() == name.to_lowercase()
    }) {
        return Err("Persona names must be unique.".to_string());
    }
    let active_id = registry.active_persona_id.clone();
    let profile = registry
        .personas
        .iter_mut()
        .find(|persona| persona.persona_id == persona_id)
        .ok_or_else(|| "Persona was not found.".to_string())?;
    profile.display_name = name;
    Ok(PersonaSummary {
        persona_id: profile.persona_id.clone(),
        display_name: profile.display_name.clone(),
        created_at: profile.created_at,
        last_used_at: profile.last_used_at,
        active: profile.persona_id == active_id,
    })
}

pub fn activate_persona(
    registry: &mut PersonaRegistry,
    persona_id: &str,
) -> Result<PersonaSummary, String> {
    let profile = registry
        .personas
        .iter_mut()
        .find(|persona| persona.persona_id == persona_id)
        .ok_or_else(|| "Persona was not found.".to_string())?;
    profile.last_used_at = Some(now_epoch_seconds());
    registry.active_persona_id = persona_id.to_string();
    Ok(PersonaSummary {
        persona_id: profile.persona_id.clone(),
        display_name: profile.display_name.clone(),
        created_at: profile.created_at,
        last_used_at: profile.last_used_at,
        active: true,
    })
}

pub fn remove_inactive_persona(
    registry: &mut PersonaRegistry,
    persona_id: &str,
    confirmation: &str,
) -> Result<PersonaProfile, String> {
    if registry.personas.len() <= 1 {
        return Err("The final Persona cannot be deleted.".to_string());
    }
    if registry.active_persona_id == persona_id {
        return Err("Switch away from a Persona before deleting it.".to_string());
    }
    let index = registry
        .personas
        .iter()
        .position(|persona| persona.persona_id == persona_id)
        .ok_or_else(|| "Persona was not found.".to_string())?;
    let profile = &registry.personas[index];
    if confirmation != format!("DELETE {}", profile.display_name) {
        return Err(format!("Type DELETE {} to confirm.", profile.display_name));
    }
    Ok(registry.personas.remove(index))
}

pub fn profile_overrides(profile: &PersonaProfile) -> Result<BTreeMap<String, String>, String> {
    let values = parse_env(
        &fs::read_to_string(&profile.config_path)
            .map_err(|_| "Could not read the active Persona configuration.".to_string())?,
    );
    for required in [
        "PERSONA_ID",
        "PERSONA_DISPLAY_NAME",
        "PERSONA_IDENTITY_PATH",
        "DATABASE_URL",
        "DATABASE_AUTH_TOKEN",
    ] {
        if values.get(required).is_none_or(String::is_empty) {
            return Err("The active Persona configuration is incomplete.".to_string());
        }
    }
    Ok(values)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temporary_root(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "mindcore-persona-registry-{label}-{}",
            new_persona_id().expect("id")
        ));
        fs::create_dir_all(&path).expect("temp directory");
        path
    }

    #[test]
    fn legacy_migration_is_idempotent_and_preserves_external_paths() {
        let root = temporary_root("migration");
        let identity = root.join("legacy-identity.txt");
        let database = root.join("legacy.db");
        fs::write(&identity, "generic identity").expect("identity");
        fs::write(&database, "existing database bytes").expect("database");
        let config = root.join("mindcore.env");
        fs::write(
            &config,
            format!(
                "DATABASE_URL=\"{}\"\nDATABASE_AUTH_TOKEN=\"token\"\nPERSONA_DISPLAY_NAME=\"Jarvis\"\nPERSONA_IDENTITY_PATH=\"{}\"\n",
                database.display(),
                identity.display()
            ),
        )
        .expect("config");

        let first = migrate_legacy_config(&config)
            .expect("migration")
            .expect("registry");
        let second = migrate_legacy_config(&config)
            .expect("second migration")
            .expect("registry");
        assert_eq!(first, second);
        assert_eq!(first.personas.len(), 1);
        assert_eq!(first.personas[0].display_name, "Jarvis");
        assert_eq!(
            fs::read_to_string(&database).expect("database"),
            "existing database bytes"
        );
        assert_eq!(
            fs::read_to_string(&identity).expect("identity"),
            "generic identity"
        );
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn registry_requires_immutable_unique_ids_and_names() {
        let profile = PersonaProfile {
            persona_id: "stable-id".into(),
            display_name: "Jarvis".into(),
            identity_path: "identity".into(),
            config_path: "config".into(),
            created_at: 1,
            last_used_at: None,
        };
        let mut registry = PersonaRegistry {
            version: REGISTRY_VERSION,
            active_persona_id: "stable-id".into(),
            personas: vec![profile.clone()],
        };
        assert!(validate_registry(&registry).is_ok());
        registry.personas.push(PersonaProfile {
            persona_id: "other-id".into(),
            display_name: "JARVIS".into(),
            ..profile
        });
        assert!(validate_registry(&registry).is_err());
        assert!(persona_directory(Path::new("root/mindcore.env"), "../escape").is_err());
    }

    #[test]
    fn secure_atomic_write_replaces_existing_file_without_relaxing_permissions() {
        let root = temporary_root("atomic-write");
        let config = root.join("personas.json");
        secure_atomic_write(&config, "first").expect("first write");
        secure_atomic_write(&config, "second").expect("replacement write");
        assert_eq!(fs::read_to_string(&config).expect("config"), "second");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&config)
                    .expect("metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn rename_switch_restart_and_confirmed_delete_preserve_isolation() {
        let root = temporary_root("lifecycle");
        let config = root.join("mindcore.env");
        fs::write(&config, "LLM_PROVIDER=gemini\n").expect("global config");
        let database_a = root.join("a.db");
        let database_b = root.join("b.db");
        fs::write(&database_a, "A_ONLY_DATABASE").expect("database A");
        fs::write(&database_b, "B_ONLY_DATABASE").expect("database B");
        let profile = |persona_id: &str, display_name: &str| PersonaProfile {
            persona_id: persona_id.into(),
            display_name: display_name.into(),
            identity_path: root
                .join(format!("{persona_id}.txt"))
                .to_string_lossy()
                .into(),
            config_path: root
                .join(format!("{persona_id}.env"))
                .to_string_lossy()
                .into(),
            created_at: 1,
            last_used_at: None,
        };
        let mut registry = PersonaRegistry {
            version: REGISTRY_VERSION,
            active_persona_id: "persona-a".into(),
            personas: vec![profile("persona-a", "Jarvis"), profile("persona-b", "Nova")],
        };

        let renamed = rename_persona(&mut registry, "persona-a", "JARVIS PRIME").expect("rename");
        assert_eq!(renamed.persona_id, "persona-a");
        assert!(rename_persona(&mut registry, "persona-b", "jarvis prime").is_err());
        activate_persona(&mut registry, "persona-b").expect("activate B");
        save_registry(&config, &registry).expect("save");
        let mut restarted = load_registry(&config).expect("load").expect("registry");
        assert_eq!(restarted.active_persona_id, "persona-b");
        assert!(remove_inactive_persona(&mut restarted, "persona-a", "wrong").is_err());
        remove_inactive_persona(&mut restarted, "persona-a", "DELETE JARVIS PRIME")
            .expect("confirmed delete");
        assert_eq!(
            fs::read_to_string(&database_a).expect("database A"),
            "A_ONLY_DATABASE"
        );
        assert_eq!(
            fs::read_to_string(&database_b).expect("database B"),
            "B_ONLY_DATABASE"
        );
        fs::remove_dir_all(root).expect("cleanup");
    }
}
