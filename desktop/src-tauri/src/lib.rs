// LinkNote 데스크톱 — 앱 시작 시 백엔드(FastAPI)를 자동 실행한다.
use std::env;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

fn backend_running() -> bool {
    let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn find_project_root() -> Option<PathBuf> {
    if let Ok(manifest_dir) = env::var("CARGO_MANIFEST_DIR") {
        let mut path = PathBuf::from(manifest_dir);
        if path.ends_with("src-tauri") {
            path.pop();
        }
        if path.ends_with("desktop") {
            path.pop();
        }
        if path.join("api_server.py").exists() {
            return Some(path);
        }
    }

    let mut current = env::current_dir().ok()?;
    loop {
        if current.join("api_server.py").exists() {
            return Some(current);
        }
        if !current.pop() {
            break;
        }
    }
    None
}

fn find_python_interpreter(project_root: &PathBuf) -> String {
    let candidates = [
        project_root.join(".venv/bin/python"),
        project_root.join("venv/bin/python"),
        project_root.join(".venv/bin/python3"),
        project_root.join("venv/bin/python3"),
    ];

    for candidate in candidates {
        if candidate.exists() {
            return candidate.to_string_lossy().into_owned();
        }
    }

    "python3".to_string()
}

fn start_backend() {
    if backend_running() {
        return;
    }

    let Some(project_root) = find_project_root() else {
        return;
    };

    let python = find_python_interpreter(&project_root);
    let _ = Command::new(&python)
        .args(["-m", "uvicorn", "api_server:app", "--host", "127.0.0.1", "--port", "8000"])
        .current_dir(&project_root)
        .spawn();

    // 백엔드 포트가 열릴 때까지 최대 ~30초 대기
    for _ in 0..60 {
        if backend_running() {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|_app| {
            start_backend();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
