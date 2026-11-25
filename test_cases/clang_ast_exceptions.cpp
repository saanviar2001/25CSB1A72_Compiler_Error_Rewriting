namespace app {
    enum Status { ok, warning, error };

    int run() {
        try {
            throw 2;
        } catch (int exc) {
            return exc == 2 ? static_cast<int>(Status::warning) : static_cast<int>(Status::error);
        }
    }
}

int main() {
    return app::run();
}
