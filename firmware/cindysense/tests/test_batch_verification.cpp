// Host test: g++ -std=c++11 test_batch_verification.cpp -o test_batch_verification
#include "../batch_verification.h"

#include <assert.h>
#include <stdio.h>
#include <string>

static const char *const BATCH_ID = "cs-d4533244d57c-bc7994df-00000071";

static std::string quoteJson(const std::string &value) {
  std::string result = "\"";
  for (char ch : value) {
    if (ch == '\\' || ch == '"') result += '\\';
    result += ch;
  }
  result += '"';
  return result;
}

static std::string responseFor(const std::string &content, bool conAsString,
                               const char *resourceName = BATCH_ID) {
  const std::string con = conAsString ? quoteJson(content) : content;
  return std::string("{\"m2m:cin\":{\"rn\":") + quoteJson(resourceName) +
         ",\"con\":" + con + "}}";
}

static VerificationResult check(int status, const std::string &body,
                                const SensorSample *samples, size_t count) {
  char decoded[2048];
  return verifyBatchHttpResponse(status, body.c_str(), body.size(), BATCH_ID,
                                 samples, count, decoded, sizeof(decoded));
}

int main() {
  const SensorSample samples[] = {
      {12340, 1.250f, -2.500f, 9.806f, 10.125f, -20.250f, 30.375f},
      {12360, 1.500f, -2.250f, 9.556f, 10.500f, -20.000f, 30.000f},
  };
  const std::string matching =
      std::string("{\"deviceId\":\"CindySense_ESP32\",\"batchId\":\"") +
      BATCH_ID +
      "\",\"samples\":[{\"t\":12340,\"ax\":1.250,\"ay\":-2.500,\"az\":9.806,\"gx\":10.125,\"gy\":-20.250,\"gz\":30.375},{\"t\":12360,\"ax\":1.500,\"ay\":-2.250,\"az\":9.556,\"gx\":10.500,\"gy\":-20.000,\"gz\":30.000}]}";

  assert(check(200, responseFor(matching, false), samples, 2) == VERIFY_MATCH);
  assert(check(200, responseFor(matching, true), samples, 2) == VERIFY_MATCH);

  std::string changedValue = matching;
  changedValue.replace(changedValue.find("1.250"), 5, "1.750");
  assert(check(200, responseFor(changedValue, false), samples, 2) ==
         VERIFY_MISMATCH);

  std::string changedTimestamp = matching;
  changedTimestamp.replace(changedTimestamp.find("12340"), 5, "12341");
  assert(check(200, responseFor(changedTimestamp, false), samples, 2) ==
         VERIFY_MISMATCH);

  std::string changedBatch = matching;
  changedBatch.replace(changedBatch.find(BATCH_ID), strlen(BATCH_ID),
                       "cs-another-batch");
  assert(check(200, responseFor(changedBatch, false), samples, 2) ==
         VERIFY_MISMATCH);
  assert(check(200, responseFor(matching, false, "cs-other-resource"),
               samples, 2) == VERIFY_MISMATCH);

  assert(check(200, responseFor(matching, false), samples, 1) ==
         VERIFY_MISMATCH);
  assert(check(404, "", samples, 2) == VERIFY_NOT_FOUND);
  assert(check(408, "", samples, 2) == VERIFY_UNKNOWN);
  assert(check(503, "{}", samples, 2) == VERIFY_UNKNOWN);
  assert(check(200, "not-json", samples, 2) == VERIFY_MISMATCH);

  puts("batch verification tests passed");
  return 0;
}
