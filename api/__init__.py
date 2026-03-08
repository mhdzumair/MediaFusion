from utils.exception_tracker import install_exception_handler

# Install once when API package loads so app and workers share exception tracking.
install_exception_handler()
