use mediafusion_api::models::user_data::UserData;

fn main() {
    let raw_json = std::fs::read_to_string("/tmp/user_config.json").unwrap();
    let raw: serde_json::Value = serde_json::from_str(&raw_json).unwrap();
    match serde_json::from_value::<UserData>(raw.clone()) {
        Ok(ud) => {
            println!("Deser OK");
            println!("streaming_providers: {}", ud.streaming_providers.len());
            for sp in &ud.streaming_providers {
                println!(
                    "  provider: name={} service={} token={}",
                    sp.name,
                    sp.service,
                    sp.token.is_some()
                );
            }
            println!("stream_template: {}", ud.stream_template.is_some());
            println!("max_streams: {}", ud.max_streams);
        }
        Err(e) => {
            println!("Deser ERROR: {}", e);
        }
    }
}
