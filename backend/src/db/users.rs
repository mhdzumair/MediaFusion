use sqlx::PgPool;

use super::types::UserRole;

/// Load a user's role from the database.
pub async fn get_user_role(pool: &PgPool, user_id: i32) -> Option<UserRole> {
    sqlx::query_scalar("SELECT role FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
}

pub fn is_mod_or_admin(role: UserRole) -> bool {
    matches!(role, UserRole::Moderator | UserRole::Admin)
}

pub fn is_admin(role: UserRole) -> bool {
    role == UserRole::Admin
}
