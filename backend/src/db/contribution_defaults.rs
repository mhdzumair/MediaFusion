//! Default contribution settings (Python `ContributionSettings` parity when DB row is absent).

pub const POINTS_PER_STREAM_EDIT: i64 = 3;
pub const CONTRIBUTOR_THRESHOLD: i64 = 10;
pub const TRUSTED_THRESHOLD: i64 = 50;
pub const EXPERT_THRESHOLD: i64 = 200;

pub const AUTO_APPROVAL_THRESHOLD: i32 = 25;
pub const POINTS_PER_METADATA_EDIT: i32 = 5;
pub const POINTS_FOR_REJECTION_PENALTY: i32 = -2;
pub const BROKEN_REPORT_THRESHOLD: i32 = 3;
pub const MAX_PENDING_SUGGESTIONS_PER_USER: i32 = 20;
