#include "llama.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

static std::vector<llama_token> tokenize(const llama_model * model, const std::string & text) {
    int32_t count = llama_tokenize(model, text.data(), static_cast<int32_t>(text.size()),
                                   nullptr, 0, false, false);
    if (count >= 0) {
        return {};
    }
    std::vector<llama_token> tokens(static_cast<size_t>(-count));
    count = llama_tokenize(model, text.data(), static_cast<int32_t>(text.size()),
                           tokens.data(), static_cast<int32_t>(tokens.size()), false, false);
    if (count < 0) {
        std::cerr << "tokenization failed\n";
        std::exit(2);
    }
    tokens.resize(static_cast<size_t>(count));
    return tokens;
}

static std::vector<std::string> breakers_for(const std::string & mode) {
    if (mode == "default")          return {"\n", ":", "\"", "*"};
    if (mode == "no-colon")         return {"\n", "\"", "*"};
    if (mode == "no-newline")       return {":", "\"", "*"};
    if (mode == "no-colon-newline") return {"\"", "*"};
    if (mode == "none")             return {};
    std::cerr << "unknown breaker mode\n";
    std::exit(2);
}

int main(int argc, char ** argv) {
    if (argc != 6) {
        std::cerr << "usage: dry_replay_v26 MODEL MULTIPLIER BASE ALLOWED BREAKERS\n";
        return 2;
    }
    const float multiplier = std::stof(argv[2]);
    const float base = std::stof(argv[3]);
    const int allowed = std::stoi(argv[4]);
    const auto breaker_strings = breakers_for(argv[5]);
    std::vector<const char *> breakers;
    for (const auto & value : breaker_strings) breakers.push_back(value.c_str());

    llama_backend_init();
    auto params = llama_model_default_params();
    params.vocab_only = true;
    llama_model * model = llama_model_load_from_file(argv[1], params);
    if (!model) return 2;

    const std::string text((std::istreambuf_iterator<char>(std::cin)),
                           std::istreambuf_iterator<char>());
    const auto tokens = tokenize(model, text);
    auto * dry = llama_sampler_init_dry(
        llama_model_get_vocab(model), multiplier, base, allowed, -1,
        breakers.empty() ? nullptr : breakers.data(), breakers.size());
    if (!dry) return 2;

    size_t penalized = 0;
    double penalty_sum = 0.0;
    float penalty_max = 0.0f;
    for (const auto token : tokens) {
        llama_token_data candidate{token, 0.0f, 0.0f};
        llama_token_data_array candidates{&candidate, 1, -1, false};
        llama_sample_dry(nullptr, dry, &candidates);
        const float penalty = -candidate.logit;
        if (penalty > 1e-6f) {
            ++penalized;
            penalty_sum += penalty;
            penalty_max = std::max(penalty_max, penalty);
        }
        llama_sampler_dry_accept(dry, token);
    }

    std::cout << "tokens=" << tokens.size()
              << " penalized=" << penalized
              << " fraction=" << (tokens.empty() ? 0.0 : double(penalized) / tokens.size())
              << " penalty_sum=" << penalty_sum
              << " penalty_max=" << penalty_max << "\n";
    llama_sampler_dry_free(dry);
    llama_free_model(model);
    llama_backend_free();
}
