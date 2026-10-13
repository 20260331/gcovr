int shared_covered();
int core_covered();
int net_covered();

int main() {
    return shared_covered() + core_covered() + net_covered() - 3;
}
