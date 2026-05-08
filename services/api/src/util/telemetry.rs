pub fn init() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("mediafusion_api=info".parse().unwrap()),
        )
        .init();
}
