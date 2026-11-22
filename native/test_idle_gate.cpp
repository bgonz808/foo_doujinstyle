#include "idle_gate.hpp"
#include <cassert>
#include <chrono>
int main() {
  using namespace std::chrono;
  doujin::IdleGate gate;
  const auto start = steady_clock::time_point{};
  assert(!gate.ready(29999, start));
  assert(gate.ready(30000, start));
  assert(!gate.ready(60000, start+seconds(9)));
  assert(!gate.ready(0, start+seconds(10)));
  assert(gate.ready(60000, start+seconds(10)));
  assert(!gate.ready(30000, start+seconds(19)));
  assert(gate.ready(30000, start+seconds(20)));
}
