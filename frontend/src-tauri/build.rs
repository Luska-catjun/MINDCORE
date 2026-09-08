fn main() {
    println!("cargo:rustc-check-cfg=cfg(mindcore_updater_disabled)");
    println!("cargo:rerun-if-env-changed=MINDCORE_UPDATER_DISABLED");
    if std::env::var("MINDCORE_UPDATER_DISABLED").as_deref() == Ok("1") {
        println!("cargo:rustc-cfg=mindcore_updater_disabled");
    }
    tauri_build::build()
}
