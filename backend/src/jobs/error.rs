use thiserror::Error;

#[derive(Debug, Error)]
pub enum JobError {
    #[error("database error: {0}")]
    Db(#[from] sqlx::Error),

    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("http error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("cancelled")]
    Cancelled,

    #[error("{0}")]
    Other(String),
}

impl JobError {
    pub fn other(msg: impl Into<String>) -> Self {
        Self::Other(msg.into())
    }

    /// Transient errors are retried; permanent ones go straight to dead.
    pub fn is_transient(&self) -> bool {
        match self {
            Self::Db(_) | Self::Http(_) => true,
            Self::Cancelled => false,
            Self::Serde(_) => false,
            Self::Other(_) => true,
        }
    }
}
