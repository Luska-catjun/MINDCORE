use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const REGISTRY_VERSION: u32 = 1;
pub const MAX_AVATAR_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PersonaLifecycleState {
    #[default]
    Ready,
    Provisioning,
    Deleting,
}

impl PersonaLifecycleState {
    fn is_ready(&self) -> bool {
        matches!(self, Self::Ready)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PersonaProfile {
    pub persona_id: String,
    pub display_name: String,
    pub identity_path: String,
    pub config_path: String,
    pub created_at: u64,
    pub last_used_at: Option<u64>,
    #[serde(default)]
    pub avatar_extension: Option<String>,
    #[serde(default, skip_serializing_if = "PersonaLifecycleState::is_ready")]
    pub lifecycle_state: PersonaLifecycleState,
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
    pub avatar_extension: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PersonaProvisioningInput<'a> {
    pub display_name: &'a str,
    pub identity: &'a str,
    pub database_url: &'a str,
    pub database_auth_token: &'a str,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PersonaDeleteResult {
    pub deleted: bool,
    pub cleanup_pending: bool,
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

fn avatar_extension_is_valid(extension: &str) -> bool {
    matches!(extension, "png" | "jpg" | "webp")
}

fn avatar_format(bytes: &[u8]) -> Option<(&'static str, &'static str)> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some(("png", "image/png"))
    } else if bytes.len() >= 3 && bytes[0..3] == [0xff, 0xd8, 0xff] {
        Some(("jpg", "image/jpeg"))
    } else if bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        Some(("webp", "image/webp"))
    } else {
        None
    }
}

pub fn avatar_path(global_config: &Path, profile: &PersonaProfile) -> Result<Option<PathBuf>, String> {
    let Some(extension) = profile.avatar_extension.as_deref() else {
        return Ok(None);
    };
    if !avatar_extension_is_valid(extension) {
        return Err("The Persona avatar metadata is invalid.".to_string());
    }
    Ok(Some(
        persona_directory(global_config, &profile.persona_id)?.join(format!("avatar.{extension}")),
    ))
}

pub fn read_avatar(
    global_config: &Path,
    profile: &PersonaProfile,
) -> Result<Option<(String, Vec<u8>)>, String> {
    let Some(path) = avatar_path(global_config, profile)? else {
        return Ok(None);
    };
    if !path.is_file() {
        return Ok(None);
    }
    let bytes = fs::read(&path).map_err(|_| "Could not read the Persona avatar.".to_string())?;
    let Some((extension, mime_type)) = avatar_format(&bytes) else {
        return Err("The Persona avatar file is unsupported.".to_string());
    };
    if bytes.len() > MAX_AVATAR_BYTES || profile.avatar_extension.as_deref() != Some(extension) {
        return Err("The Persona avatar file is invalid.".to_string());
    }
    Ok(Some((mime_type.to_string(), bytes)))
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

const PERSONA_DATABASE_KEYS: [&str; 5] = [
    "DATABASE_URL",
    "DATABASE_AUTH_TOKEN",
    "OLD_DATABASE_URL",
    "TURSO_DB_URL",
    "SUPABASE_DB_URL",
];

// The registry is authoritative for these values. They remain in persona.env
// only as a compatibility projection for older sidecars and installations.
const PERSONA_COMPATIBILITY_KEYS: [&str; 3] = [
    "PERSONA_ID",
    "PERSONA_DISPLAY_NAME",
    "PERSONA_IDENTITY_PATH",
];

pub fn global_config_values(source: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let mut values = source.clone();
    values.remove("DATABASE_BACKEND");
    for key in PERSONA_DATABASE_KEYS
        .iter()
        .chain(PERSONA_COMPATIBILITY_KEYS.iter())
    {
        values.remove(*key);
    }
    values
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
    for key in PERSONA_DATABASE_KEYS {
        if let Some(value) = source.get(key) {
            lines.push(format!("{key}={}", env_value(value)));
        }
    }
    lines.join("\n") + "\n"
}

pub fn secure_atomic_write(path: &Path, text: &str) -> Result<(), String> {
    secure_atomic_write_bytes(path, text.as_bytes())
}

pub fn secure_atomic_write_bytes(path: &Path, bytes: &[u8]) -> Result<(), String> {
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
        .write_all(bytes)
        .and_then(|_| temporary.flush())
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|_| "Could not write MindCore configuration.".to_string())?;
    temporary
        .persist(path)
        .map(|_| ())
        .map_err(|_| "Could not finalize MindCore configuration.".to_string())
}

pub fn replace_avatar(
    global_config: &Path,
    profile: &mut PersonaProfile,
    bytes: &[u8],
) -> Result<(), String> {
    if bytes.is_empty() || bytes.len() > MAX_AVATAR_BYTES {
        return Err("Choose a PNG, JPEG, or WebP image under 2 MB.".to_string());
    }
    let Some((extension, _mime_type)) = avatar_format(bytes) else {
        return Err("Choose a valid PNG, JPEG, or WebP image.".to_string());
    };
    let previous_path = avatar_path(global_config, profile)?;
    let directory = persona_directory(global_config, &profile.persona_id)?;
    fs::create_dir_all(&directory)
        .map_err(|_| "Could not create the Persona avatar directory.".to_string())?;
    let target = directory.join(format!("avatar.{extension}"));
    secure_atomic_write_bytes(&target, bytes)?;
    profile.avatar_extension = Some(extension.to_string());
    if let Some(previous_path) = previous_path.filter(|path| path != &target) {
        if previous_path.is_file() {
            fs::remove_file(previous_path)
                .map_err(|_| "Could not replace the previous Persona avatar.".to_string())?;
        }
    }
    Ok(())
}

pub fn remove_avatar(global_config: &Path, profile: &mut PersonaProfile) -> Result<(), String> {
    let path = avatar_path(global_config, profile)?;
    if let Some(path) = path {
        let directory = persona_directory(global_config, &profile.persona_id)?;
        if !path.starts_with(&directory) {
            return Err("The Persona avatar is outside its managed directory.".to_string());
        }
        if path.is_file() {
            fs::remove_file(path).map_err(|_| "Could not remove the Persona avatar.".to_string())?;
        }
    }
    profile.avatar_extension = None;
    Ok(())
}

pub fn cleanup_managed_persona_files(
    global_config: &Path,
    profile: &mut PersonaProfile,
) -> Result<(), String> {
    let managed = persona_directory(global_config, &profile.persona_id)?;
    let config_path = PathBuf::from(&profile.config_path);
    let identity_path = PathBuf::from(&profile.identity_path);
    remove_avatar(global_config, profile)?;
    if !config_path.starts_with(&managed) {
        return Ok(());
    }
    if config_path.exists() {
        fs::remove_file(&config_path).map_err(|_| {
            "Persona was removed, but its managed configuration could not be cleaned up."
                .to_string()
        })?;
    }
    if identity_path.starts_with(&managed) && identity_path.exists() {
        fs::remove_file(&identity_path).map_err(|_| {
            "Persona was removed, but its managed identity could not be cleaned up.".to_string()
        })?;
    }
    if managed.is_dir() {
        fs::remove_dir(&managed).map_err(|_| {
            "Persona was removed, but its non-empty profile directory was preserved."
                .to_string()
        })?;
    }
    Ok(())
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

fn validate_database_source(source: &BTreeMap<String, String>) -> Result<(), String> {
    if source
        .get("DATABASE_URL")
        .is_none_or(|value| value.trim().is_empty())
        || source
            .get("DATABASE_AUTH_TOKEN")
            .is_none_or(|value| value.trim().is_empty())
    {
        return Err("The Persona database configuration is incomplete.".to_string());
    }
    Ok(())
}

pub fn persist_profile_update_with<WriteProfile, SaveRegistry>(
    global_config: &Path,
    registry: &mut PersonaRegistry,
    persona_id: &str,
    display_name: &str,
    database_source: &BTreeMap<String, String>,
    mut write_profile: WriteProfile,
    mut save: SaveRegistry,
) -> Result<PersonaSummary, String>
where
    WriteProfile: FnMut(&Path, &str) -> Result<(), String>,
    SaveRegistry: FnMut(&Path, &PersonaRegistry) -> Result<(), String>,
{
    validate_database_source(database_source)?;
    let mut candidate = registry.clone();
    let summary = rename_persona(&mut candidate, persona_id, display_name)?;
    let profile = candidate
        .personas
        .iter()
        .find(|profile| profile.persona_id == persona_id)
        .ok_or_else(|| "Persona was not found.".to_string())?;
    let profile_path = PathBuf::from(&profile.config_path);
    let previous_text = fs::read_to_string(&profile_path)
        .map_err(|_| "Could not read the Persona configuration.".to_string())?;
    let next_text = profile_config_text(
        &profile.persona_id,
        &profile.display_name,
        Path::new(&profile.identity_path),
        database_source,
    );
    write_profile(&profile_path, &next_text)?;
    if let Err(error) = save(global_config, &candidate) {
        let _ = write_profile(&profile_path, &previous_text);
        return Err(error);
    }
    *registry = candidate;
    Ok(summary)
}

pub fn create_initial_registry_with<WriteProfile, SaveRegistry>(
    global_config: &Path,
    display_name: &str,
    identity_path: &Path,
    database_source: &BTreeMap<String, String>,
    mut write_profile: WriteProfile,
    mut save: SaveRegistry,
) -> Result<PersonaRegistry, String>
where
    WriteProfile: FnMut(&Path, &str) -> Result<(), String>,
    SaveRegistry: FnMut(&Path, &PersonaRegistry) -> Result<(), String>,
{
    let name = validate_display_name(display_name)?;
    validate_database_source(database_source)?;
    let persona_id = new_persona_id()?;
    let directory = persona_directory(global_config, &persona_id)?;
    let config_path = directory.join("persona.env");
    if let Err(error) = write_profile(
        &config_path,
        &profile_config_text(&persona_id, &name, identity_path, database_source),
    ) {
        let _ = fs::remove_dir_all(&directory);
        return Err(error);
    }
    let created_at = now_epoch_seconds();
    let registry = PersonaRegistry {
        version: REGISTRY_VERSION,
        active_persona_id: persona_id.clone(),
        personas: vec![PersonaProfile {
            persona_id,
            display_name: name,
            identity_path: identity_path.to_string_lossy().into_owned(),
            config_path: config_path.to_string_lossy().into_owned(),
            created_at,
            last_used_at: Some(created_at),
            avatar_extension: None,
            lifecycle_state: PersonaLifecycleState::Ready,
        }],
    };
    if let Err(error) = save(global_config, &registry) {
        let _ = fs::remove_dir_all(&directory);
        return Err(error);
    }
    Ok(registry)
}

#[allow(clippy::too_many_arguments)]
pub fn provision_persona_with<WriteProfile, SaveRegistry, Preflight, Initialize>(
    global_config: &Path,
    registry: &mut PersonaRegistry,
    input: PersonaProvisioningInput<'_>,
    mut write_profile: WriteProfile,
    mut save: SaveRegistry,
    mut preflight: Preflight,
    mut initialize: Initialize,
) -> Result<PersonaSummary, String>
where
    WriteProfile: FnMut(&Path, &str) -> Result<(), String>,
    SaveRegistry: FnMut(&Path, &PersonaRegistry) -> Result<(), String>,
    Preflight: FnMut(&Path) -> Result<(), String>,
    Initialize: FnMut(&Path) -> Result<(), String>,
{
    let name = validate_display_name(input.display_name)?;
    if input.identity.trim().is_empty()
        || input.identity.len() > 64 * 1024
        || input.identity.contains('\0')
    {
        return Err("Identity must be UTF-8 plain text under 64 KB.".to_string());
    }
    let source = BTreeMap::from([
        ("DATABASE_URL".to_string(), input.database_url.trim().to_string()),
        (
            "DATABASE_AUTH_TOKEN".to_string(),
            input.database_auth_token.trim().to_string(),
        ),
    ]);
    validate_database_source(&source)?;

    let previous_registry = registry.clone();
    let existing_pending = registry.personas.iter().position(|profile| {
        profile.lifecycle_state == PersonaLifecycleState::Provisioning
            && profile.display_name.eq_ignore_ascii_case(&name)
    });
    if existing_pending.is_none()
        && registry
            .personas
            .iter()
            .any(|profile| profile.display_name.eq_ignore_ascii_case(&name))
    {
        return Err("Persona names must be unique.".to_string());
    }

    let is_new = existing_pending.is_none();
    let persona_id = existing_pending
        .map(|index| registry.personas[index].persona_id.clone())
        .map(Ok)
        .unwrap_or_else(new_persona_id)?;
    let directory = persona_directory(global_config, &persona_id)?;
    let identity_path = directory.join("identity.txt");
    let config_path = directory.join("persona.env");
    let created_at = existing_pending
        .map(|index| registry.personas[index].created_at)
        .unwrap_or_else(now_epoch_seconds);
    let profile = PersonaProfile {
        persona_id: persona_id.clone(),
        display_name: name.clone(),
        identity_path: identity_path.to_string_lossy().into_owned(),
        config_path: config_path.to_string_lossy().into_owned(),
        created_at,
        last_used_at: None,
        avatar_extension: None,
        lifecycle_state: PersonaLifecycleState::Provisioning,
    };

    if let Err(error) = write_profile(&identity_path, input.identity) {
        if is_new {
            let _ = fs::remove_dir_all(&directory);
        }
        return Err(error);
    }
    if let Err(error) = write_profile(
        &config_path,
        &profile_config_text(&persona_id, &name, &identity_path, &source),
    ) {
        if is_new {
            let _ = fs::remove_dir_all(&directory);
        }
        return Err(error);
    }

    let index = if let Some(index) = existing_pending {
        registry.personas[index] = profile;
        index
    } else {
        registry.personas.push(profile);
        registry.personas.len() - 1
    };
    if let Err(error) = save(global_config, registry) {
        *registry = previous_registry;
        if is_new {
            let _ = fs::remove_dir_all(&directory);
        }
        return Err(error);
    }

    preflight(&config_path)?;
    initialize(&config_path)?;

    let mut ready = registry.clone();
    ready.personas[index].lifecycle_state = PersonaLifecycleState::Ready;
    if let Err(error) = save(global_config, &ready) {
        return Err(error);
    }
    *registry = ready;
    Ok(PersonaSummary {
        persona_id,
        display_name: name,
        created_at,
        last_used_at: None,
        active: false,
        avatar_extension: None,
    })
}

pub fn delete_persona_with<Cleanup, SaveRegistry>(
    global_config: &Path,
    registry: &mut PersonaRegistry,
    persona_id: &str,
    confirmation: &str,
    mut cleanup: Cleanup,
    mut save: SaveRegistry,
) -> Result<PersonaDeleteResult, String>
where
    Cleanup: FnMut(&Path, &mut PersonaProfile) -> Result<(), String>,
    SaveRegistry: FnMut(&Path, &PersonaRegistry) -> Result<(), String>,
{
    let mut deleting = registry.clone();
    let profile = remove_inactive_persona(&mut deleting, persona_id, confirmation)?;
    let index = registry
        .personas
        .iter()
        .position(|item| item.persona_id == profile.persona_id)
        .ok_or_else(|| "Persona was not found.".to_string())?;
    deleting.personas.insert(index, PersonaProfile {
        lifecycle_state: PersonaLifecycleState::Deleting,
        ..profile
    });
    save(global_config, &deleting)?;
    *registry = deleting;

    let mut cleanup_profile = registry.personas[index].clone();
    if cleanup(global_config, &mut cleanup_profile).is_err() {
        return Ok(PersonaDeleteResult { deleted: true, cleanup_pending: true });
    }
    let mut completed = registry.clone();
    completed.personas.remove(index);
    if save(global_config, &completed).is_err() {
        return Ok(PersonaDeleteResult { deleted: true, cleanup_pending: true });
    }
    *registry = completed;
    Ok(PersonaDeleteResult { deleted: true, cleanup_pending: false })
}

pub fn recover_deleting_personas_with<Cleanup, SaveRegistry>(
    global_config: &Path,
    registry: &mut PersonaRegistry,
    mut cleanup: Cleanup,
    mut save: SaveRegistry,
) -> Result<usize, String>
where
    Cleanup: FnMut(&Path, &mut PersonaProfile) -> Result<(), String>,
    SaveRegistry: FnMut(&Path, &PersonaRegistry) -> Result<(), String>,
{
    let mut recovered = registry.clone();
    let mut removed = 0;
    recovered.personas.retain_mut(|profile| {
        if profile.lifecycle_state != PersonaLifecycleState::Deleting {
            return true;
        }
        if cleanup(global_config, profile).is_ok() {
            removed += 1;
            false
        } else {
            true
        }
    });
    if removed > 0 {
        save(global_config, &recovered)?;
        *registry = recovered;
    }
    Ok(removed)
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
        if persona
            .avatar_extension
            .as_deref()
            .is_some_and(|extension| !avatar_extension_is_valid(extension))
        {
            return Err("The Persona avatar metadata is invalid.".to_string());
        }
        if !ids.insert(persona.persona_id.clone())
            || !names.insert(persona.display_name.to_lowercase())
        {
            return Err("Persona identifiers and names must be unique.".to_string());
        }
    }
    if !registry.personas.iter().any(|persona| {
        persona.persona_id == registry.active_persona_id && persona.lifecycle_state.is_ready()
    }) {
        return Err("The active Persona is missing from the registry.".to_string());
    }
    if !registry
        .personas
        .iter()
        .any(|persona| persona.lifecycle_state.is_ready())
    {
        return Err("The Persona registry has no ready Persona.".to_string());
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
            avatar_extension: None,
            lifecycle_state: PersonaLifecycleState::Ready,
        }],
    };
    if let Err(error) = save_registry(global_config, &registry) {
        let _ = fs::remove_dir_all(&directory);
        return Err(error);
    }
    Ok(Some(registry))
}

pub fn active_profile(registry: &PersonaRegistry) -> Result<&PersonaProfile, String> {
    registry
        .personas
        .iter()
        .find(|persona| {
            persona.persona_id == registry.active_persona_id
                && persona.lifecycle_state.is_ready()
        })
        .ok_or_else(|| "The active Persona profile is unavailable.".to_string())
}

pub fn summaries(registry: &PersonaRegistry) -> Vec<PersonaSummary> {
    registry
        .personas
        .iter()
        .filter(|persona| persona.lifecycle_state.is_ready())
        .map(|persona| PersonaSummary {
            persona_id: persona.persona_id.clone(),
            display_name: persona.display_name.clone(),
            created_at: persona.created_at,
            last_used_at: persona.last_used_at,
            active: persona.persona_id == registry.active_persona_id,
            avatar_extension: persona.avatar_extension.clone(),
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
        .find(|persona| persona.persona_id == persona_id && persona.lifecycle_state.is_ready())
        .ok_or_else(|| "Persona was not found.".to_string())?;
    profile.display_name = name;
    Ok(PersonaSummary {
        persona_id: profile.persona_id.clone(),
        display_name: profile.display_name.clone(),
        created_at: profile.created_at,
        last_used_at: profile.last_used_at,
        active: profile.persona_id == active_id,
        avatar_extension: profile.avatar_extension.clone(),
    })
}

pub fn activate_persona(
    registry: &mut PersonaRegistry,
    persona_id: &str,
) -> Result<PersonaSummary, String> {
    let profile = registry
        .personas
        .iter_mut()
        .find(|persona| persona.persona_id == persona_id && persona.lifecycle_state.is_ready())
        .ok_or_else(|| "Persona was not found.".to_string())?;
    profile.last_used_at = Some(now_epoch_seconds());
    registry.active_persona_id = persona_id.to_string();
    Ok(PersonaSummary {
        persona_id: profile.persona_id.clone(),
        display_name: profile.display_name.clone(),
        created_at: profile.created_at,
        last_used_at: profile.last_used_at,
        active: true,
        avatar_extension: profile.avatar_extension.clone(),
    })
}

pub fn remove_inactive_persona(
    registry: &mut PersonaRegistry,
    persona_id: &str,
    confirmation: &str,
) -> Result<PersonaProfile, String> {
    if registry
        .personas
        .iter()
        .filter(|persona| persona.lifecycle_state.is_ready())
        .count()
        <= 1
    {
        return Err("The final Persona cannot be deleted.".to_string());
    }
    if registry.active_persona_id == persona_id {
        return Err("Switch away from a Persona before deleting it.".to_string());
    }
    let index = registry
        .personas
        .iter()
        .position(|persona| persona.persona_id == persona_id && persona.lifecycle_state.is_ready())
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
    for required in ["DATABASE_URL", "DATABASE_AUTH_TOKEN"] {
        if values.get(required).is_none_or(String::is_empty) {
            return Err("The active Persona configuration is incomplete.".to_string());
        }
    }
    let mut overrides = BTreeMap::from([("DATABASE_BACKEND".to_string(), "turso".to_string())]);
    for key in PERSONA_DATABASE_KEYS {
        if let Some(value) = values.get(key) {
            overrides.insert(key.to_string(), value.clone());
        }
    }
    Ok(overrides)
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
    fn global_user_display_name_is_not_copied_into_persona_profile() {
        let source = BTreeMap::from([
            ("DATABASE_URL".into(), "libsql://persona-a".into()),
            ("DATABASE_AUTH_TOKEN".into(), "token".into()),
            ("USER_DISPLAY_NAME".into(), "Luska".into()),
        ]);
        let text = profile_config_text(
            "persona-a",
            "Jarvis",
            Path::new("/managed/identity.txt"),
            &source,
        );

        assert!(text.contains("PERSONA_DISPLAY_NAME=\"Jarvis\""));
        assert!(text.contains("DATABASE_URL=\"libsql://persona-a\""));
        assert!(!text.contains("USER_DISPLAY_NAME"));
    }

    fn png_bytes(marker: u8) -> Vec<u8> {
        [b"\x89PNG\r\n\x1a\n".as_slice(), &[marker]].concat()
    }

    fn jpeg_bytes(marker: u8) -> Vec<u8> {
        [b"\xff\xd8\xff".as_slice(), &[marker]].concat()
    }

    fn webp_bytes(marker: u8) -> Vec<u8> {
        [b"RIFF\0\0\0\0WEBP".as_slice(), &[marker]].concat()
    }

    fn managed_profile(root: &Path, persona_id: &str, display_name: &str) -> PersonaProfile {
        let directory = persona_directory(&root.join("mindcore.env"), persona_id).expect("directory");
        fs::create_dir_all(&directory).expect("managed directory");
        let identity_path = directory.join("identity.txt");
        let config_path = directory.join("persona.env");
        fs::write(&identity_path, format!("identity-{persona_id}")).expect("identity");
        let source = BTreeMap::from([
            ("DATABASE_URL".into(), format!("file:{persona_id}.db")),
            (
                "DATABASE_AUTH_TOKEN".into(),
                format!("synthetic-test-token-{persona_id}"),
            ),
        ]);
        fs::write(
            &config_path,
            profile_config_text(persona_id, display_name, &identity_path, &source),
        )
        .expect("config");
        PersonaProfile {
            persona_id: persona_id.into(),
            display_name: display_name.into(),
            identity_path: identity_path.to_string_lossy().into_owned(),
            config_path: config_path.to_string_lossy().into_owned(),
            created_at: 1,
            last_used_at: None,
            avatar_extension: None,
            lifecycle_state: PersonaLifecycleState::Ready,
        }
    }

    fn two_persona_registry(root: &Path) -> PersonaRegistry {
        PersonaRegistry {
            version: REGISTRY_VERSION,
            active_persona_id: "persona-a".into(),
            personas: vec![
                managed_profile(root, "persona-a", "Jarvis"),
                managed_profile(root, "persona-b", "Nova"),
            ],
        }
    }

    fn provisioning_input<'a>(name: &'a str) -> PersonaProvisioningInput<'a> {
        PersonaProvisioningInput {
            display_name: name,
            identity: "generic test identity",
            database_url: "file:persona.db",
            database_auth_token: "synthetic-secret-marker",
        }
    }

    #[test]
    fn registry_is_the_runtime_authority_and_global_config_is_persona_independent() {
        let root = temporary_root("authority");
        let mut registry = two_persona_registry(&root);
        registry.personas[0].display_name = "Registry Name".into();
        let profile_text = fs::read_to_string(&registry.personas[0].config_path).expect("profile");
        assert!(profile_text.contains("PERSONA_DISPLAY_NAME=\"Jarvis\""));

        let overrides = profile_overrides(&registry.personas[0]).expect("runtime database");
        assert_eq!(overrides.get("DATABASE_URL").map(String::as_str), Some("file:persona-a.db"));
        assert!(!overrides.contains_key("PERSONA_ID"));
        assert!(!overrides.contains_key("PERSONA_DISPLAY_NAME"));
        assert!(!overrides.contains_key("PERSONA_IDENTITY_PATH"));
        assert_eq!(active_profile(&registry).expect("active").display_name, "Registry Name");

        let global = global_config_values(&BTreeMap::from([
            ("LLM_PROVIDER".into(), "gemini".into()),
            ("USER_DISPLAY_NAME".into(), "User".into()),
            ("DATABASE_URL".into(), "file:legacy.db".into()),
            ("DATABASE_AUTH_TOKEN".into(), "legacy-token".into()),
            ("PERSONA_ID".into(), "legacy-id".into()),
            ("PERSONA_DISPLAY_NAME".into(), "Legacy".into()),
            ("PERSONA_IDENTITY_PATH".into(), "legacy.txt".into()),
        ]));
        assert_eq!(global.get("LLM_PROVIDER").map(String::as_str), Some("gemini"));
        assert_eq!(global.get("USER_DISPLAY_NAME").map(String::as_str), Some("User"));
        assert!(!global.contains_key("DATABASE_URL"));
        assert!(!global.contains_key("DATABASE_AUTH_TOKEN"));
        assert!(!global.contains_key("PERSONA_DISPLAY_NAME"));
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn durable_rename_synchronizes_compatibility_projection_and_survives_reload() {
        let root = temporary_root("rename-projection");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        save_registry(&config, &registry).expect("initial registry");
        let source = parse_env(
            &fs::read_to_string(&registry.personas[0].config_path).expect("profile"),
        );

        persist_profile_update_with(
            &config,
            &mut registry,
            "persona-a",
            "JARVIS-2",
            &source,
            secure_atomic_write,
            save_registry,
        )
        .expect("rename");

        let restarted = load_registry(&config).expect("load").expect("registry");
        assert_eq!(active_profile(&restarted).expect("active").display_name, "JARVIS-2");
        let projected = parse_env(
            &fs::read_to_string(&restarted.personas[0].config_path).expect("profile"),
        );
        assert_eq!(projected.get("PERSONA_DISPLAY_NAME").map(String::as_str), Some("JARVIS-2"));
        assert_eq!(projected.get("PERSONA_ID").map(String::as_str), Some("persona-a"));
        assert_eq!(
            projected.get("PERSONA_IDENTITY_PATH").map(String::as_str),
            Some(restarted.personas[0].identity_path.as_str()),
        );
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn rename_registry_failure_restores_profile_and_keeps_canonical_name() {
        let root = temporary_root("rename-save-failure");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let original = fs::read_to_string(&registry.personas[0].config_path).expect("profile");
        let source = parse_env(&original);

        let error = persist_profile_update_with(
            &config,
            &mut registry,
            "persona-a",
            "Changed",
            &source,
            secure_atomic_write,
            |_path, _registry| Err("registry-save-failed".into()),
        )
        .expect_err("save must fail");

        assert_eq!(error, "registry-save-failed");
        assert_eq!(registry.personas[0].display_name, "Jarvis");
        assert_eq!(fs::read_to_string(&registry.personas[0].config_path).expect("profile"), original);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn provisioning_success_is_hidden_until_preflight_initialize_and_commit_finish() {
        use std::cell::RefCell;
        let root = temporary_root("provision-success");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let snapshots = RefCell::new(Vec::new());

        let summary = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            |path, state| {
                snapshots.borrow_mut().push(summaries(state).len());
                save_registry(path, state)
            },
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect("provision");

        assert_eq!(&*snapshots.borrow(), &[2, 3]);
        assert_eq!(summaries(&registry).len(), 3);
        assert!(!summary.active);
        assert_eq!(active_profile(&registry).expect("active").persona_id, "persona-a");
        let profile = registry
            .personas
            .iter()
            .find(|profile| profile.persona_id == summary.persona_id)
            .expect("created profile");
        assert_eq!(profile.lifecycle_state, PersonaLifecycleState::Ready);
        assert!(Path::new(&profile.config_path).is_file());
        assert!(Path::new(&profile.identity_path).is_file());
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn provisioning_failures_never_publish_a_ready_persona_and_retry_reuses_identity() {
        let root = temporary_root("provision-retry");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        save_registry(&config, &registry).expect("initial registry");

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            save_registry,
            |_path| Err("database-preflight-failed".into()),
            |_path| panic!("initialize must not run"),
        )
        .expect_err("preflight failure");
        assert_eq!(error, "database-preflight-failed");
        assert_eq!(summaries(&registry).len(), 2);
        assert_eq!(registry.active_persona_id, "persona-a");
        let pending_id = registry
            .personas
            .iter()
            .find(|profile| profile.lifecycle_state == PersonaLifecycleState::Provisioning)
            .expect("pending")
            .persona_id
            .clone();

        let summary = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            save_registry,
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect("retry");
        assert_eq!(summary.persona_id, pending_id);
        assert_eq!(summaries(&registry).len(), 3);
        assert_eq!(registry.active_persona_id, "persona-a");
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn initialize_and_final_registry_failures_remain_retryable_provisioning_state() {
        use std::cell::Cell;
        let root = temporary_root("provision-stage-failures");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            save_registry,
            |_path| Ok(()),
            |_path| Err("database-initialize-failed".into()),
        )
        .expect_err("initialize failure");
        assert_eq!(error, "database-initialize-failed");
        assert_eq!(summaries(&registry).len(), 2);

        let save_count = Cell::new(0);
        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            |path, state| {
                save_count.set(save_count.get() + 1);
                if save_count.get() == 2 {
                    Err("ready-registry-save-failed".into())
                } else {
                    save_registry(path, state)
                }
            },
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect_err("ready save failure");
        assert_eq!(error, "ready-registry-save-failed");
        assert_eq!(summaries(&registry).len(), 2);
        assert!(registry
            .personas
            .iter()
            .any(|profile| profile.lifecycle_state == PersonaLifecycleState::Provisioning));

        provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            save_registry,
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect("final retry");
        assert_eq!(summaries(&registry).len(), 3);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn local_write_and_initial_registry_failures_leave_existing_personas_unchanged() {
        use std::cell::Cell;
        let root = temporary_root("provision-local-failures");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let original = registry.clone();
        let write_count = Cell::new(0);

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            |_path, _text| Err("identity-write-failed".into()),
            save_registry,
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect_err("identity write failure");
        assert_eq!(error, "identity-write-failed");
        assert_eq!(registry, original);

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            |_path, _text| {
                write_count.set(write_count.get() + 1);
                if write_count.get() == 2 { Err("profile-write-failed".into()) } else { Ok(()) }
            },
            save_registry,
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect_err("profile write failure");
        assert_eq!(error, "profile-write-failed");
        assert_eq!(registry, original);

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            |_path, _registry| Err("registry-save-failed".into()),
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect_err("registry save failure");
        assert_eq!(error, "registry-save-failed");
        assert_eq!(registry, original);
        assert_eq!(summaries(&registry).len(), 2);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn managed_directory_creation_failure_does_not_publish_or_mutate_a_persona() {
        let root = temporary_root("provision-directory-failure");
        let blocked_parent = root.join("not-a-directory");
        fs::write(&blocked_parent, "blocking file").expect("blocking file");
        let config = blocked_parent.join("mindcore.env");
        let registry_root = temporary_root("provision-directory-registry");
        let mut registry = two_persona_registry(&registry_root);
        let original = registry.clone();

        let error = provision_persona_with(
            &config,
            &mut registry,
            provisioning_input("Orion"),
            secure_atomic_write,
            save_registry,
            |_path| Ok(()),
            |_path| Ok(()),
        )
        .expect_err("directory creation must fail");

        assert_eq!(
            error,
            "Could not create MindCore configuration directory."
        );
        assert_eq!(registry, original);
        assert_eq!(summaries(&registry).len(), 2);
        fs::remove_dir_all(root).expect("cleanup blocked root");
        fs::remove_dir_all(registry_root).expect("cleanup registry root");
    }

    #[test]
    fn delete_cleanup_failure_is_hidden_safe_and_recovered_without_cross_persona_damage() {
        let root = temporary_root("delete-recovery");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        save_registry(&config, &registry).expect("initial registry");
        let a_config = fs::read_to_string(&registry.personas[0].config_path).expect("A profile");
        let b_directory = persona_directory(&config, "persona-b").expect("B directory");

        let result = delete_persona_with(
            &config,
            &mut registry,
            "persona-b",
            "DELETE Nova",
            |_path, _profile| Err("filesystem-locked".into()),
            save_registry,
        )
        .expect("logical delete");
        assert!(result.deleted);
        assert!(result.cleanup_pending);
        assert_eq!(summaries(&registry).len(), 1);
        assert_eq!(registry.active_persona_id, "persona-a");
        assert!(b_directory.exists());
        assert_eq!(fs::read_to_string(&registry.personas[0].config_path).expect("A profile"), a_config);

        assert_eq!(
            recover_deleting_personas_with(
                &config,
                &mut registry,
                cleanup_managed_persona_files,
                save_registry,
            )
            .expect("recovery"),
            1,
        );
        assert!(!b_directory.exists());
        assert_eq!(registry.personas.len(), 1);
        assert_eq!(active_profile(&registry).expect("active").persona_id, "persona-a");
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn delete_success_removes_only_the_inactive_managed_profile() {
        let root = temporary_root("delete-success");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        save_registry(&config, &registry).expect("initial registry");
        let active_profile_text =
            fs::read_to_string(&registry.personas[0].config_path).expect("active profile");
        let deleted_directory = persona_directory(&config, "persona-b").expect("B directory");

        let result = delete_persona_with(
            &config,
            &mut registry,
            "persona-b",
            "DELETE Nova",
            cleanup_managed_persona_files,
            save_registry,
        )
        .expect("delete");

        assert_eq!(
            result,
            PersonaDeleteResult {
                deleted: true,
                cleanup_pending: false,
            }
        );
        assert!(!deleted_directory.exists());
        assert_eq!(registry.personas.len(), 1);
        assert_eq!(registry.active_persona_id, "persona-a");
        assert_eq!(
            fs::read_to_string(&registry.personas[0].config_path).expect("active profile"),
            active_profile_text
        );
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn delete_registry_failure_leaves_registry_and_managed_files_untouched() {
        let root = temporary_root("delete-registry-failure");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let original = registry.clone();
        let deleted_directory = persona_directory(&config, "persona-b").expect("B directory");

        let error = delete_persona_with(
            &config,
            &mut registry,
            "persona-b",
            "DELETE Nova",
            |_path, _profile| panic!("cleanup must not run"),
            |_path, _registry| Err("registry-save-failed".into()),
        )
        .expect_err("registry save failure");

        assert_eq!(error, "registry-save-failed");
        assert_eq!(registry, original);
        assert!(deleted_directory.is_dir());
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn delete_rejects_active_and_final_personas_without_mutation() {
        let root = temporary_root("delete-invariants");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let original = registry.clone();
        assert!(delete_persona_with(
            &config,
            &mut registry,
            "persona-a",
            "DELETE Jarvis",
            cleanup_managed_persona_files,
            save_registry,
        )
        .is_err());
        assert_eq!(registry, original);

        registry.personas.pop();
        let only = registry.clone();
        assert!(delete_persona_with(
            &config,
            &mut registry,
            "persona-a",
            "DELETE Jarvis",
            cleanup_managed_persona_files,
            save_registry,
        )
        .is_err());
        assert_eq!(registry, only);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn lifecycle_errors_do_not_echo_database_credentials() {
        let root = temporary_root("secret-errors");
        let config = root.join("mindcore.env");
        let mut registry = two_persona_registry(&root);
        let input = provisioning_input("Orion");
        let error = provision_persona_with(
            &config,
            &mut registry,
            input.clone(),
            secure_atomic_write,
            save_registry,
            |_path| Err("database-preflight-failed".into()),
            |_path| Ok(()),
        )
        .expect_err("preflight failure");
        assert!(!error.contains(input.database_url));
        assert!(!error.contains(input.database_auth_token));
        fs::remove_dir_all(root).expect("cleanup");
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
            avatar_extension: None,
            lifecycle_state: PersonaLifecycleState::Ready,
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
    fn legacy_registry_without_avatar_metadata_loads_with_fallback() {
        let root = temporary_root("legacy-avatar");
        let config = root.join("mindcore.env");
        fs::write(
            registry_path(&config).expect("registry path"),
            r#"{"version":1,"active_persona_id":"persona-a","personas":[{"persona_id":"persona-a","display_name":"Jarvis","identity_path":"identity","config_path":"config","created_at":1,"last_used_at":null}]}"#,
        )
        .expect("legacy registry");

        let registry = load_registry(&config).expect("load").expect("registry");
        assert_eq!(registry.personas[0].avatar_extension, None);
        assert_eq!(
            registry.personas[0].lifecycle_state,
            PersonaLifecycleState::Ready
        );
        assert_eq!(summaries(&registry)[0].avatar_extension, None);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn pending_persona_cannot_become_active_and_malformed_profiles_fail_safely() {
        let root = temporary_root("pending-invariants");
        let mut registry = two_persona_registry(&root);
        registry.personas[1].lifecycle_state = PersonaLifecycleState::Provisioning;
        let previous = registry.clone();

        assert!(activate_persona(&mut registry, "persona-b").is_err());
        assert_eq!(registry, previous);
        registry.active_persona_id = "persona-b".into();
        assert!(validate_registry(&registry).is_err());

        let incomplete = previous.personas[0].clone();
        fs::write(
            &incomplete.config_path,
            "DATABASE_URL=\"file:persona-a.db\"\n",
        )
        .expect("incomplete profile");
        let error = profile_overrides(&incomplete).expect_err("profile must be rejected");
        assert_eq!(error, "The active Persona configuration is incomplete.");
        assert!(!error.contains("file:persona-a.db"));
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn avatars_are_persona_isolated_persistent_and_cleaned_up() {
        let root = temporary_root("avatars");
        let config = root.join("mindcore.env");
        let external_source = root.join("external-source.png");
        let external_bytes = png_bytes(b'A');
        fs::write(&external_source, &external_bytes).expect("external source");
        let mut registry = PersonaRegistry {
            version: REGISTRY_VERSION,
            active_persona_id: "persona-a".into(),
            personas: vec![
                managed_profile(&root, "persona-a", "Jarvis"),
                managed_profile(&root, "persona-b", "Nova"),
            ],
        };

        replace_avatar(&config, &mut registry.personas[0], &external_bytes).expect("A avatar");
        replace_avatar(&config, &mut registry.personas[1], &webp_bytes(b'B')).expect("B avatar");
        assert_eq!(fs::read(&external_source).expect("source unchanged"), external_bytes);
        assert_eq!(read_avatar(&config, &registry.personas[0]).expect("read A").unwrap().0, "image/png");
        assert_eq!(read_avatar(&config, &registry.personas[1]).expect("read B").unwrap().0, "image/webp");
        save_registry(&config, &registry).expect("save registry");

        let mut restarted = load_registry(&config).expect("restart").expect("registry");
        rename_persona(&mut restarted, "persona-a", "JARVIS-2").expect("rename");
        assert_eq!(restarted.personas[0].avatar_extension.as_deref(), Some("png"));
        let original_a = avatar_path(&config, &restarted.personas[0]).expect("path").expect("path");
        replace_avatar(&config, &mut restarted.personas[0], &jpeg_bytes(b'C')).expect("replace A");
        assert!(!original_a.exists());
        assert_eq!(read_avatar(&config, &restarted.personas[0]).expect("read replacement").unwrap().0, "image/jpeg");
        assert_eq!(read_avatar(&config, &restarted.personas[1]).expect("read B after A replacement").unwrap().0, "image/webp");

        remove_avatar(&config, &mut restarted.personas[0]).expect("remove A");
        assert_eq!(restarted.personas[0].avatar_extension, None);
        assert_eq!(read_avatar(&config, &restarted.personas[0]).expect("fallback"), None);

        cleanup_managed_persona_files(&config, &mut restarted.personas[1]).expect("delete B files");
        assert!(!persona_directory(&config, "persona-b").expect("B directory").exists());
        assert_eq!(fs::read(&external_source).expect("source still unchanged"), external_bytes);
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn avatar_rejects_unsupported_and_oversize_input() {
        let root = temporary_root("avatar-validation");
        let config = root.join("mindcore.env");
        let mut profile = managed_profile(&root, "persona-a", "Jarvis");
        assert!(replace_avatar(&config, &mut profile, b"not-an-image").is_err());
        let mut oversized = png_bytes(b'X');
        oversized.resize(MAX_AVATAR_BYTES + 1, b'X');
        assert!(replace_avatar(&config, &mut profile, &oversized).is_err());
        fs::remove_dir_all(root).expect("cleanup");
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
        let global_config = "LLM_PROVIDER=gemini\nUSER_DISPLAY_NAME=Luska\n";
        fs::write(&config, global_config).expect("global config");
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
            avatar_extension: None,
            lifecycle_state: PersonaLifecycleState::Ready,
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
        assert_eq!(fs::read_to_string(&config).expect("global user remains"), global_config);
        let mut restarted = load_registry(&config).expect("load").expect("registry");
        assert_eq!(restarted.active_persona_id, "persona-b");
        assert!(remove_inactive_persona(&mut restarted, "persona-a", "wrong").is_err());
        remove_inactive_persona(&mut restarted, "persona-a", "DELETE JARVIS PRIME")
            .expect("confirmed delete");
        assert_eq!(fs::read_to_string(&config).expect("global user remains"), global_config);
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
