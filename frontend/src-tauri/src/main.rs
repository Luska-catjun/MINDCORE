#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar_lifecycle;

use serde::{Deserialize, Serialize};
use sidecar_lifecycle::{SidecarLifecycle, StartDecision, StopDecision};
use std::{fs, io::{Read, Write}, net::{SocketAddr, TcpStream}, path::{Path, PathBuf}, process::Command, sync::{Arc, Mutex}, thread, time::{Duration, Instant}};
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};

const DESKTOP_PORT: u16 = 8765;
const MAX_IDENTITY_BYTES: usize = 64 * 1024;
const SHUTDOWN_CAPABILITY_ENV: &str = "MINDCORE_DESKTOP_SHUTDOWN_CAPABILITY";
const SHUTDOWN_CAPABILITY_HEADER: &str = "X-MindCore-Desktop-Shutdown";
const DESKTOP_INSTANCE_HEADER: &str = "X-MindCore-Desktop-Instance";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const STARTUP_POLL_INTERVAL: Duration = Duration::from_millis(250);
#[cfg(test)]
const UPDATER_PLUGIN_ENABLED: bool = !cfg!(mindcore_updater_disabled);
struct Sidecar { lifecycle: Arc<Mutex<SidecarLifecycle<CommandChild>>> }
#[derive(Serialize)] struct SetupStatus { configured: bool, config_path: String, identity_path: String }
#[derive(Serialize)] struct ConfigMetadata { database_url: String, llm_provider: String, persona_display_name: String, turso_token_configured: bool, gemini_key_configured: bool, groq_key_configured: bool }
#[derive(Deserialize)] struct SetupDraft { database_url: String, database_auth_token: String, llm_provider: String, api_key: String, persona_display_name: String, #[serde(default)] preserve_database_auth_token: bool, #[serde(default)] preserve_api_key: bool, #[serde(default)] preserve_identity: bool }

// Tauri resolves the target-triple source binary configured in `externalBin`
// to this packaged runtime name (and supplies `.exe` on Windows).
fn sidecar(app:&AppHandle)->Result<tauri_plugin_shell::process::Command,String>{app.shell().sidecar("mindcore-backend").map_err(|_|"MindCore sidecar is unavailable for this platform.".into())}
fn config_path(app:&AppHandle)->Result<PathBuf,String>{if let Some(p)=std::env::var_os("MINDCORE_ENV_FILE"){return Ok(p.into())}if cfg!(debug_assertions){return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().and_then(|p|p.parent()).ok_or_else(||"MindCore source root is unavailable".to_string())?.join(".env"))}app.path().app_config_dir().map_err(|e|e.to_string()).map(|d|d.join("mindcore.env"))}
fn identity_path(app:&AppHandle)->Result<PathBuf,String>{let config=config_path(app)?;Ok(config.parent().ok_or_else(||"MindCore configuration directory is unavailable".to_string())?.join("identity").join("identity.txt"))}
fn config_is_complete(app:&AppHandle)->Result<bool,String>{let config=config_path(app)?;let identity=identity_path(app)?;let Ok(text)=fs::read_to_string(config) else{return Ok(false)};Ok(["DATABASE_URL=","DATABASE_AUTH_TOKEN=","LLM_PROVIDER=","PERSONA_DISPLAY_NAME=","PERSONA_IDENTITY_PATH="].iter().all(|key|text.lines().any(|line|line.starts_with(key)&&line[key.len()..].trim_matches('"').trim().len()>0))&&identity.is_file())}
fn env_value_from(text:&str,key:&str)->Option<String>{text.lines().find_map(|line|line.strip_prefix(&format!("{key}=")).map(|value|value.trim().trim_matches('"').to_string())).filter(|value|!value.is_empty())}
fn existing_secret(app:&AppHandle,key:&str)->Option<String>{fs::read_to_string(config_path(app).ok()?).ok().and_then(|text|env_value_from(&text,key))}
fn validate_database_draft(d:&SetupDraft)->Result<(),String>{if d.database_url.trim().is_empty()||(!d.preserve_database_auth_token&&d.database_auth_token.trim().is_empty()){return Err("Complete all required database fields.".into())}Ok(())}
fn validate_llm_draft(d:&SetupDraft)->Result<(),String>{if !matches!(d.llm_provider.as_str(),"gemini"|"groq"){return Err("Choose Gemini or Groq.".into())}if !d.preserve_api_key&&d.api_key.trim().is_empty(){return Err("Complete all required language model fields.".into())}Ok(())}
fn validate_draft(d:&SetupDraft)->Result<(),String>{let name=d.persona_display_name.trim();if name.is_empty()||name.chars().count()>80||name.chars().any(char::is_control){return Err("Choose a Persona name of up to 80 characters.".into())}validate_database_draft(d)?;validate_llm_draft(d)}
fn validate_setup_action(action:&str,d:&SetupDraft)->Result<(),String>{match action{"database"=>validate_database_draft(d),"llm"=>validate_llm_draft(d),"classify"|"initialize"=>validate_draft(d),_=>Err("Unsupported setup action.".into())}}
fn env_value(v:&str)->String{format!("\"{}\"",v.replace('\\',"\\\\").replace('"',"\\\"").replace('\n',""))}
fn draft_env(app:&AppHandle,d:&SetupDraft)->Result<String,String>{let identity=identity_path(app)?;let token=if d.preserve_database_auth_token{existing_secret(app,"DATABASE_AUTH_TOKEN").ok_or_else(||"Stored database token is unavailable.".to_string())?}else{d.database_auth_token.trim().to_string()};let active_key=if d.llm_provider=="gemini"{"GEMINI_API_KEY"}else{"GROQ_API_KEY"};let api_key=if d.preserve_api_key{existing_secret(app,active_key).ok_or_else(||"Stored provider key is unavailable.".to_string())?}else{d.api_key.trim().to_string()};let mut gemini=existing_secret(app,"GEMINI_API_KEY");let mut groq=existing_secret(app,"GROQ_API_KEY");if active_key=="GEMINI_API_KEY"{gemini=Some(api_key)}else{groq=Some(api_key)};let keys=format!("{}{}",gemini.map(|value|format!("GEMINI_API_KEY={}\n",env_value(&value))).unwrap_or_default(),groq.map(|value|format!("GROQ_API_KEY={}\n",env_value(&value))).unwrap_or_default());Ok(format!("DATABASE_BACKEND=turso\nDATABASE_URL={}\nDATABASE_AUTH_TOKEN={}\nLLM_PROVIDER={}\nLLM_FALLBACK_PROVIDER=\n{}PERSONA_DISPLAY_NAME={}\nPERSONA_IDENTITY_PATH={}\n",env_value(d.database_url.trim()),env_value(&token),d.llm_provider,keys,env_value(d.persona_display_name.trim()),env_value(&identity.to_string_lossy())))}
fn atomic_write(path:&Path,text:&str)->Result<(),String>{let dir=path.parent().ok_or_else(||"Configuration directory is unavailable".to_string())?;fs::create_dir_all(dir).map_err(|_|"Could not create MindCore configuration directory.".to_string())?;let tmp=dir.join(format!(".{}.{}.tmp",path.file_name().and_then(|n|n.to_str()).unwrap_or("mindcore"),std::process::id()));fs::write(&tmp,text).map_err(|_|"Could not save MindCore configuration.".to_string())?;#[cfg(unix)]{use std::os::unix::fs::PermissionsExt;let _=fs::set_permissions(&tmp,fs::Permissions::from_mode(0o600));}fs::rename(tmp,path).map_err(|_|"Could not finalize MindCore configuration.".to_string())}
fn setup_action(app:&AppHandle,action:&str,draft:&SetupDraft)->Result<String,String>{validate_setup_action(action,draft)?;let config=config_path(app)?;let staging=config.parent().ok_or_else(||"Configuration directory is unavailable".to_string())?.join(format!(".mindcore-setup-{}.env",std::process::id()));atomic_write(&staging,&draft_env(app,draft)?)?;let output=tauri::async_runtime::block_on(sidecar(app)?.args(["--setup-action",action,"--config"]).arg(&staging).output()).map_err(|_|"MindCore setup service could not start.".to_string());let _=fs::remove_file(&staging);let output=output?;if output.status.success(){Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())}else{Err("Setup validation failed. Check the values and try again.".into())}}
fn ensure_desktop_auth(app:&AppHandle)->Result<(),String>{let config=config_path(app)?;let output=tauri::async_runtime::block_on(sidecar(app)?.args(["--desktop-ensure-auth","--config"]).arg(&config).output()).map_err(|_|"MindCore desktop authentication could not be prepared.".to_string())?;if output.status.success(){Ok(())}else{Err("MindCore desktop authentication could not be prepared.".into())}}
fn desktop_session(app:&AppHandle)->Result<String,String>{ensure_desktop_auth(app)?;let config=config_path(app)?;let output=tauri::async_runtime::block_on(sidecar(app)?.args(["--desktop-print-session","--config"]).arg(&config).output()).map_err(|_|"MindCore desktop session could not start.".to_string())?;if output.status.success(){let token=String::from_utf8_lossy(&output.stdout).trim().to_string();if !token.is_empty(){return Ok(token)}}Err("MindCore desktop session could not start.".into())}
fn new_shutdown_capability()->Result<String,String>{let mut bytes=[0_u8;32];getrandom::fill(&mut bytes).map_err(|_|"MindCore backend authorization could not be created.".to_string())?;Ok(bytes.iter().map(|byte|format!("{byte:02x}")).collect())}
fn lifecycle_request(method:&str,path:&str,capability:&str)->String{format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{SHUTDOWN_CAPABILITY_HEADER}: {capability}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")}
fn readiness_response_is_current(response:&[u8],capability:&str)->bool{let Ok(text)=std::str::from_utf8(response)else{return false};let mut lines=text.split("\r\n");let status_ok=lines.next().and_then(|line|line.split_whitespace().nth(1))==Some("204");status_ok&&lines.any(|line|line.split_once(':').is_some_and(|(name,value)|name.eq_ignore_ascii_case(DESKTOP_INSTANCE_HEADER)&&value.trim()==capability))}
fn request_instance_readiness(capability:&str)->bool{let address=SocketAddr::from(([127,0,0,1],DESKTOP_PORT));let Ok(mut stream)=TcpStream::connect_timeout(&address,Duration::from_millis(150))else{return false};let _=stream.set_read_timeout(Some(Duration::from_millis(150)));let _=stream.set_write_timeout(Some(Duration::from_millis(150)));if stream.write_all(lifecycle_request("GET","/_desktop/ready",capability).as_bytes()).is_err(){return false}let mut response=Vec::with_capacity(256);let mut chunk=[0_u8;128];while response.len()<1024{match stream.read(&mut chunk){Ok(0)=>break,Ok(size)=>{response.extend_from_slice(&chunk[..size]);if response.windows(4).any(|window|window==b"\r\n\r\n"){break}}Err(_)=>return false}}readiness_response_is_current(&response,capability)}
fn start_sidecar(app:&AppHandle)->Result<(),String>{if !config_is_complete(app)?{return Err("MindCore setup is incomplete.".into())}ensure_desktop_auth(app)?;let command=sidecar(app)?;let config=config_path(app)?;let shutdown_capability=new_shutdown_capability()?;let state=app.state::<Sidecar>();let generation=match state.lifecycle.lock().expect("sidecar lifecycle lock").begin_start(){StartDecision::AlreadyRunning=>return Ok(()),StartDecision::InProgress=>return Err("MindCore backend lifecycle operation is already in progress.".into()),StartDecision::Spawn{generation}=>generation};let port=DESKTOP_PORT.to_string();let pid=std::process::id().to_string();let spawned=command.args(["--port",&port,"--parent-pid",&pid]).env("MINDCORE_ENV_FILE",config).env(SHUTDOWN_CAPABILITY_ENV,&shutdown_capability).spawn();let (mut events,child)=match spawned{Ok(spawned)=>spawned,Err(_)=>{state.lifecycle.lock().expect("sidecar lifecycle lock").fail_start(generation);return Err("MindCore backend could not start.".into())}};if let Err(child)=state.lifecycle.lock().expect("sidecar lifecycle lock").attach_child(generation,child,shutdown_capability.clone()){let _=child.kill();return Err("MindCore backend start was superseded by another lifecycle operation.".into())}let lifecycle=Arc::clone(&state.lifecycle);tauri::async_runtime::spawn(async move{while let Some(event)=events.recv().await{match event{CommandEvent::Terminated(_)=>{let _=lifecycle.lock().expect("sidecar lifecycle lock").record_exit(generation,false);return}CommandEvent::Error(_)=>{let _=lifecycle.lock().expect("sidecar lifecycle lock").record_exit(generation,true);return}_=>{}}}});let deadline=Instant::now()+STARTUP_TIMEOUT;loop{if !state.lifecycle.lock().expect("sidecar lifecycle lock").is_generation_active(generation){return Err("MindCore backend exited before becoming ready.".into())}if request_instance_readiness(&shutdown_capability){if state.lifecycle.lock().expect("sidecar lifecycle lock").mark_running(generation){return Ok(())}return Err("MindCore backend lifecycle changed during startup.".into())}if Instant::now()>=deadline{break}thread::sleep(STARTUP_POLL_INTERVAL)}if let Some(child)=state.lifecycle.lock().expect("sidecar lifecycle lock").fail_start(generation){let _=child.kill();}Err("MindCore backend could not claim its local port. Another application may be using port 8765.".into())}

#[tauri::command] fn get_setup_status(app:AppHandle)->Result<SetupStatus,String>{Ok(SetupStatus{configured:config_is_complete(&app)?,config_path:config_path(&app)?.to_string_lossy().into(),identity_path:identity_path(&app)?.to_string_lossy().into()})}
#[tauri::command] fn get_config_metadata(app:AppHandle)->Result<ConfigMetadata,String>{let text=fs::read_to_string(config_path(&app)?).map_err(|_|"MindCore configuration is unavailable.".to_string())?;Ok(ConfigMetadata{database_url:env_value_from(&text,"DATABASE_URL").unwrap_or_default(),llm_provider:env_value_from(&text,"LLM_PROVIDER").unwrap_or_else(||"gemini".into()),persona_display_name:env_value_from(&text,"PERSONA_DISPLAY_NAME").unwrap_or_default(),turso_token_configured:existing_secret(&app,"DATABASE_AUTH_TOKEN").is_some(),gemini_key_configured:existing_secret(&app,"GEMINI_API_KEY").is_some(),groq_key_configured:existing_secret(&app,"GROQ_API_KEY").is_some()})}
#[tauri::command] fn run_setup_action(app:AppHandle,action:String,draft:SetupDraft)->Result<String,String>{if !matches!(action.as_str(),"database"|"llm"|"classify"|"initialize"){return Err("Unsupported setup action.".into())}setup_action(&app,&action,&draft)}
#[tauri::command] fn generic_identity_template(app:AppHandle)->Result<String,String>{let output=tauri::async_runtime::block_on(sidecar(&app)?.arg("--setup-print-generic-identity").output()).map_err(|_|"Could not read the generic identity template.".to_string())?;if output.status.success(){String::from_utf8(output.stdout).map_err(|_|"Generic identity template is invalid.".into())}else{Err("Could not read the generic identity template.".into())}}
#[tauri::command] fn save_mindcore_config(app:AppHandle,draft:SetupDraft,identity:String)->Result<(),String>{if !draft.preserve_identity&&(identity.is_empty()||identity.len()>MAX_IDENTITY_BYTES||identity.contains('\0')){return Err("Identity must be UTF-8 plain text under 64 KB.".into())}validate_draft(&draft)?;if !draft.preserve_identity{atomic_write(&identity_path(&app)?,&identity)?;}atomic_write(&config_path(&app)?,&draft_env(&app,&draft)?)}
#[tauri::command] fn start_mindcore_backend(app:AppHandle)->Result<(),String>{start_sidecar(&app)}
#[tauri::command] fn get_desktop_session(app:AppHandle)->Result<String,String>{desktop_session(&app)}
fn open_managed_path(path:&Path)->Result<(),String>{let mut command=if cfg!(target_os="windows"){Command::new("explorer")}else if cfg!(target_os="macos"){Command::new("open")}else{Command::new("xdg-open")};command.arg(path).spawn().map(|_|()).map_err(|_|"Could not open the managed MindCore file.".into())}
#[tauri::command] fn open_configuration_folder(app:AppHandle)->Result<(),String>{let path=config_path(&app)?;let directory=path.parent().ok_or_else(||"Configuration directory is unavailable".to_string())?;fs::create_dir_all(directory).map_err(|_|"Could not create MindCore configuration directory.".to_string())?;open_managed_path(directory)}
#[tauri::command] fn open_identity_file(app:AppHandle)->Result<(),String>{let path=identity_path(&app)?;if !path.is_file(){return Err("MindCore identity file does not exist yet.".into())}open_managed_path(&path)}
#[tauri::command] fn stop_mindcore_backend(app:AppHandle)->Result<(),String>{stop_sidecar(&app);Ok(())}
fn graceful_shutdown_request(capability:&str)->String{lifecycle_request("POST","/_desktop/shutdown",capability)}
fn request_graceful_shutdown(capability:&str){if let Ok(mut s)=TcpStream::connect(("127.0.0.1",DESKTOP_PORT)){let _=s.write_all(graceful_shutdown_request(capability).as_bytes());}}
fn stop_sidecar(app:&AppHandle){let Some(state)=app.try_state::<Sidecar>()else{return};let (generation,capability)=match state.lifecycle.lock().expect("sidecar lifecycle lock").begin_stop(){StopDecision::AlreadyStopped=>return,StopDecision::InProgress=>{for _ in 0..20{if state.lifecycle.lock().expect("sidecar lifecycle lock").is_inactive(){return}thread::sleep(Duration::from_millis(250));}return}StopDecision::Request{generation,capability}=>(generation,capability)};request_graceful_shutdown(&capability);for _ in 0..20{if !state.lifecycle.lock().expect("sidecar lifecycle lock").is_generation_active(generation){return}thread::sleep(Duration::from_millis(250));}if let Some(process)=state.lifecycle.lock().expect("sidecar lifecycle lock").finish_stop(generation){let _=process.kill();};}
#[cfg(test)]
mod setup_validation_tests {
    use super::*;

    fn database_step_draft() -> SetupDraft {
        SetupDraft {
            database_url: "libsql://example.turso.io".into(),
            database_auth_token: "test-token".into(),
            llm_provider: "gemini".into(),
            api_key: String::new(),
            persona_display_name: String::new(),
            preserve_database_auth_token: false,
            preserve_api_key: false,
            preserve_identity: false,
        }
    }

    #[test]
    fn database_preflight_does_not_require_later_wizard_steps() {
        assert!(validate_setup_action("database", &database_step_draft()).is_ok());
    }

    #[test]
    fn initialize_still_requires_complete_draft() {
        assert!(validate_setup_action("initialize", &database_step_draft()).is_err());
    }

    #[cfg(mindcore_updater_disabled)]
    #[test]
    fn updater_disabled_build_skips_updater_plugin_registration() {
        assert!(!UPDATER_PLUGIN_ENABLED);
    }

    #[cfg(not(mindcore_updater_disabled))]
    #[test]
    fn updater_enabled_build_keeps_updater_plugin_registration() {
        assert!(UPDATER_PLUGIN_ENABLED);
    }

    #[test]
    fn lifecycle_capability_is_sent_in_a_header_not_the_url() {
        let capability = "test-capability";
        let shutdown = graceful_shutdown_request(capability);
        let readiness = lifecycle_request("GET", "/_desktop/ready", capability);
        assert!(shutdown.starts_with("POST /_desktop/shutdown HTTP/1.1"));
        assert!(readiness.starts_with("GET /_desktop/ready HTTP/1.1"));
        assert!(shutdown.contains(&format!("{SHUTDOWN_CAPABILITY_HEADER}: {capability}")));
        assert!(readiness.contains(&format!("{SHUTDOWN_CAPABILITY_HEADER}: {capability}")));
        assert!(!shutdown.contains("/_desktop/shutdown?"));
        assert!(!readiness.contains("/_desktop/ready?"));
    }

    #[test]
    fn readiness_accepts_only_a_no_content_response_from_the_capability_route() {
        let capability = "test-capability";
        assert!(readiness_response_is_current(
            b"HTTP/1.1 204 No Content\r\nX-MindCore-Desktop-Instance: test-capability\r\n\r\n",
            capability,
        ));
        assert!(!readiness_response_is_current(
            b"HTTP/1.1 200 OK\r\nX-MindCore-Desktop-Instance: test-capability\r\n\r\n",
            capability,
        ));
        assert!(!readiness_response_is_current(
            b"HTTP/1.1 204 No Content\r\nX-MindCore-Desktop-Instance: other-capability\r\n\r\n",
            capability,
        ));
    }
}
fn main(){let app=tauri::Builder::default().plugin(tauri_plugin_shell::init()).plugin(tauri_plugin_process::init());#[cfg(not(mindcore_updater_disabled))]let app=app.plugin(tauri_plugin_updater::Builder::new().build());let app=app.invoke_handler(tauri::generate_handler![get_setup_status,get_config_metadata,run_setup_action,generic_identity_template,save_mindcore_config,start_mindcore_backend,get_desktop_session,stop_mindcore_backend,open_configuration_folder,open_identity_file]).setup(|app|{app.manage(Sidecar{lifecycle:Arc::new(Mutex::new(SidecarLifecycle::new()))});if config_is_complete(&app.handle())?{let _=start_sidecar(&app.handle());}Ok(())}).build(tauri::generate_context!()).expect("error while building MindCore desktop application");app.run(|app,event|{if matches!(event,RunEvent::ExitRequested{..}|RunEvent::Exit){stop_sidecar(app);}})}
