use std::{
    net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent, WebviewWindow};
#[cfg(unix)]
use std::os::unix::process::CommandExt;

struct BackendProcess(Mutex<Option<Child>>);

fn free_local_port() -> Result<u16, String> {
    TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("Could not reserve a local backend port: {error}"))
}

fn sidecar_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    let suffix = if cfg!(target_os = "windows") { ".exe" } else { "" };
    app.path().resource_dir().ok()
        .map(|root| root.join(format!("binaries/reasonframe-backend{suffix}")))
        .filter(|path| path.exists())
}

fn bundled_text_resource(app: &tauri::AppHandle, name: &str) -> Option<String> {
    let root = app.path().resource_dir().ok()?;
    [root.join("resources").join(name), root.join(name)]
        .into_iter()
        .find_map(|path| std::fs::read_to_string(path).ok())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

#[cfg(debug_assertions)]
fn development_backend_command() -> Result<Command, String> {
    let repository = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent().and_then(|frontend| frontend.parent())
        .ok_or("Could not resolve the development repository")?.to_path_buf();
    let mut command = Command::new(repository.join(".venv/bin/python"));
    command.args(["-m", "finance_terminal.desktop_backend"]);
    command.current_dir(repository);
    Ok(command)
}

#[cfg(not(debug_assertions))]
fn development_backend_command() -> Result<Command, String> {
    Err("The packaged local research service is missing. Reinstall Reasonframe.".into())
}

fn start_backend(app: &tauri::AppHandle, port: u16, data_dir: &std::path::Path) -> Result<Child, String> {
    let mut command = if let Some(binary) = sidecar_path(app) {
        Command::new(binary)
    } else {
        development_backend_command()?
    };
    command.args(["--port", &port.to_string(), "--data-dir", &data_dir.to_string_lossy()]);
    let home = std::env::var("HOME").unwrap_or_default();
    let inherited_path = std::env::var("PATH").unwrap_or_default();
    let mut path_entries: Vec<String> = inherited_path
        .split(':').filter(|entry| !entry.is_empty()).map(String::from).collect();
    for entry in [
        "/opt/homebrew/bin".to_string(),
        "/usr/local/bin".to_string(),
        format!("{home}/.local/bin"),
        format!("{home}/.npm-global/bin"),
        format!("{home}/.bun/bin"),
        format!("{home}/.volta/bin"),
        format!("{home}/.cargo/bin"),
        format!("{home}/Library/pnpm"),
    ] {
        if !home.is_empty() && !path_entries.contains(&entry) {
            path_entries.push(entry);
        }
    }
    command.env("PATH", path_entries.join(":"));
    if !home.is_empty() {
        command.env("HOME", home);
    }
    if let Some(value) = bundled_text_resource(app, "fred-api-key") {
        command.env("FRED_API_KEY", value);
    }
    if let Some(value) = bundled_text_resource(app, "edgar-identity") {
        command.env("EDGAR_IDENTITY", value);
    }
    #[cfg(unix)]
    command.process_group(0);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("The local research service could not start: {error}"))
}

fn wait_for_backend(port: u16, child: &mut Child) -> Result<(), String> {
    let address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&address.into(), Duration::from_millis(200)).is_ok() {
            return Ok(());
        }
        if child.try_wait().map_err(|error| error.to_string())?.is_some() {
            return Err("The local research service exited during startup. Open diagnostics and retry.".into());
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err("The local research service did not become ready in time. Retry the application.".into())
}

fn show_window(window: &WebviewWindow, port: Option<u16>, error: Option<&str>) {
    let payload = serde_json::json!({ "port": port, "error": error });
    let script = format!(
        "window.__FINANCE_API_BASE__ = {}; window.__FINANCE_BACKEND_ERROR__ = {};",
        port.map(|value| format!("'http://127.0.0.1:{value}'")).unwrap_or_else(|| "undefined".into()),
        serde_json::to_string(&payload["error"]).unwrap_or_else(|_| "null".into()),
    );
    let _ = window.eval(&script);
    let _ = window.show();
}

fn stop_backend(mut child: Child) {
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    #[cfg(windows)]
    let _ = child.kill();
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let window = app.get_webview_window("main").ok_or("Desktop window is unavailable")?;
            let data_dir = app.path().app_data_dir().map_err(|error| error.to_string())?;
            std::fs::create_dir_all(&data_dir)?;
            let port = free_local_port()?;
            match start_backend(&handle, port, &data_dir) {
                Ok(mut child) => match wait_for_backend(port, &mut child) {
                    Ok(()) => {
                        *app.state::<BackendProcess>().0.lock().unwrap() = Some(child);
                        show_window(&window, Some(port), None);
                    }
                    Err(error) => show_window(&window, None, Some(&error)),
                },
                Err(error) => show_window(&window, None, Some(&error)),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build desktop runtime");
    application.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(child) = handle.state::<BackendProcess>().0.lock().unwrap().take() {
                stop_backend(child);
            }
        }
    });
}
