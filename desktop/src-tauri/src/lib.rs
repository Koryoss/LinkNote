// LinkNote 데스크톱 — 앱 시작 시 백엔드(FastAPI)를 자동 실행한다.
use std::net::{SocketAddr, TcpStream};
use std::process::Command;
use std::time::Duration;

fn backend_running() -> bool {
    let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn start_backend() {
    if backend_running() {
        return;
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let proj = format!("{}/Desktop/LINKNOTE/study-rag-api", home);
    let py = format!("{}/venv/bin/python", proj);

    let _ = Command::new(py)
        .args(["-m", "uvicorn", "api_server:app", "--port", "8000"])
        .current_dir(&proj)
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
