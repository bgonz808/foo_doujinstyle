#pragma once
// Transitional source compatibility for existing consumers. New code uses
// DecoderIoBudget directly; no separate MP3 budget implementation remains.
#include "decoder_io_budget.hpp"
namespace doujin { using Mp3StartupBudget = DecoderIoBudget; }
