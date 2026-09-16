package protocol

import (
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"
)

// FuzzScanTopLevelString checks that untrusted frames never panic. For valid
// JSON and UTF-8 values, successful scans must agree with encoding/json.
// The scanner is partial: the full decoder rejects trailing garbage, and
// encoding/json replaces invalid UTF-8 while the scanner returns raw bytes.
func FuzzScanTopLevelString(f *testing.F) {
	// Seeds: valid frames the scanner should read on the fast path.
	f.Add([]byte(`{"type":"heartbeat","status":"idle"}`))
	f.Add([]byte(`{"type":"register"}`))
	f.Add([]byte(`{"stats":{"type":"decoy"},"models":[{"type":"inner"}],"type":"register"}`))
	f.Add([]byte(`{"a":1,"b":-2.5,"c":1e10,"e":true,"g":null,"type":"heartbeat"}`))
	f.Add([]byte(" \t\r\n{ \"a\" : 1 ,\n\"type\" :\r\"heartbeat\" }"))
	f.Add([]byte(`{"note":"say \"hi\" \\ done","type":"heartbeat"}`))

	// Seeds: absent / null / duplicate / case-variant type fields.
	f.Add([]byte(`{"status":"idle"}`))
	f.Add([]byte(`{"type":null}`))
	f.Add([]byte(`{"type":123}`))
	f.Add([]byte(`{"type":"first","type":"second"}`))
	f.Add([]byte(`{"type":"first","Type":"second"}`))
	f.Add([]byte(`{"Type":"heartbeat"}`))

	// Seeds: escaped keys/values the scanner defers.
	f.Add([]byte(`{"\u0074ype":"heartbeat"}`))
	f.Add([]byte(`{"type":"heart\u0062eat"}`))

	// Seeds: malformed / truncated / non-object / trailing-garbage / two docs.
	f.Add([]byte(``))
	f.Add([]byte(`{`))
	f.Add([]byte(`{"type":`))
	f.Add([]byte(`{"type":"heartb`))
	f.Add([]byte(`["type","heartbeat"]`))
	f.Add([]byte(`null`))
	f.Add([]byte(`{"type":"heartbeat"}trailing`))
	f.Add([]byte(`{"type":"heartbeat"} {"type":"register"}`))

	// Seeds: invalid UTF-8 inside key and value.
	f.Add([]byte("{\"type\":\"x\xff\"}"))
	f.Add([]byte("{\"type\":\"\xc3\x28\"}"))
	f.Add([]byte("{\"ty\xffpe\":\"heartbeat\"}"))

	// Seeds: bounded deep nesting (iterative in the scanner; encoding/json has
	// its own depth guard). Kept well under the length cap and small enough to
	// stay inside encoding/json's max-depth budget.
	nested := strings.Repeat(`{"a":`, 200) + `1` + strings.Repeat(`}`, 200)
	f.Add([]byte(`{"nested":` + nested + `,"type":"heartbeat"}`))
	f.Add([]byte(nested + `,"type":"x"`))

	f.Fuzz(func(t *testing.T, data []byte) {
		// Bound the body: skip large inputs so a single execution stays cheap
		// and the corpus cannot grow pathological.
		if len(data) > 4096 {
			t.Skip()
		}

		value, ok := scanTopLevelString(data, "type")

		if ok {
			var envelope struct {
				Type string `json:"type"`
			}
			// Only compare when the bytes are a JSON document encoding/json
			// accepts: the scanner is intentionally not a full validator.
			if err := json.Unmarshal(data, &envelope); err == nil && utf8.Valid([]byte(value)) {
				if envelope.Type != value {
					t.Fatalf("scanner returned %q but envelope decode returned %q for %q",
						value, envelope.Type, data)
				}
			}
		}

		// The read loop also drives the full decode over the same untrusted
		// bytes; exercise it here purely for the no-panic guarantee. Errors are
		// expected and ignored — behavioral equivalence with json.Unmarshal is
		// covered by the golden and chunk-fuzz suites.
		var pm ProviderMessage
		_ = pm.UnmarshalJSON(data)
	})
}
