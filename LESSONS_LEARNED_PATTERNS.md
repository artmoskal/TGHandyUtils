# Critical Development Patterns & Lessons Learned

## 🚨 **MANDATORY PATTERN: ALWAYS TEST IN DOCKER**

### **THE RULE:**
```bash
# EVERY SINGLE TIME after making changes:
./test.sh unit    # or ./test.sh integration
```

### **WHY THIS MATTERS:**
- **Runtime Reality Check**: Code that compiles != code that works in production
- **Dependency Verification**: Container environment catches import/dependency issues
- **Integration Testing**: Real database, real services, real behavior
- **No False Confidence**: Prevents claiming "it works" when it doesn't

---

## 🔄 **DEVELOPMENT CYCLE PATTERN**

### **CORRECT FLOW:**
1. **Make Changes** (code/config modifications)
2. **Run Tests** (`./test.sh unit` or `./test.sh integration`)
3. **Fix Failures** (iterate until all tests pass)
4. **ONLY THEN** claim completion
5. **Update Documentation** with honest status

### **ANTI-PATTERN (What I Keep Doing Wrong):**
1. Make changes
2. ~~Skip Docker testing~~
3. ~~Claim completion~~
4. ~~User finds runtime errors~~
5. Emergency fixes

---

## 📊 **TEST FAILURE ANALYSIS PATTERN**

### **Status:**
Run `./test.sh unit` and `./test.sh integration` for current pass/fail counts.
Stale counts removed — always verify live.

---

## 🎯 **"DONE" DEFINITION PATTERN**

### **NEVER CLAIM DONE UNTIL:**
```bash
✅ All unit tests pass in Docker
✅ All integration tests pass in Docker  
✅ Container starts without errors
✅ No runtime exceptions in logs
✅ Manual smoke test of changed functionality
```

### **HONEST STATUS REPORTING:**
Instead of: "✅ Section 1 Complete"
Use: "⚠️ Section 1: Code changes made, 30 test failures remaining"

---

## 🚨 **REFACTORING SAFETY PATTERN**

### **BEFORE MAKING CHANGES:**
1. **Run baseline tests**: Document current pass/fail state
2. **Identify test updates needed**: Plan which tests need updating
3. **Make changes incrementally**: Small changes, test frequently

### **DURING CHANGES:**
1. **Update tests FIRST** when changing signatures
2. **Run tests after each file changed**
3. **Fix immediately** - don't batch failures

### **AFTER CHANGES:**
1. **Zero tolerance for test failures**
2. **All tests must pass before moving on**
3. **Update documentation with actual status**

---

## 🏗️ **ARCHITECTURE CHANGE PATTERN**

### **WHEN CHANGING METHOD SIGNATURES:**
1. **Grep for all usages**: `grep -rn "method_name" tests/`
2. **Update tests first**: Change test code before implementation
3. **Verify test failures**: Confirm tests fail for right reason
4. **Implement changes**: Make actual code changes
5. **Verify test passes**: Green tests = safe refactoring

### **EXAMPLE: ServiceResult Refactoring**
```bash
# 1. Find all tuple unpacking in tests
grep -rn "success, .*= " tests/

# 2. Update test assertions FIRST
# OLD: success, message, data = service.method()
# NEW: result = service.method()
#      assert result.success
#      assert result.message

# 3. THEN update service method
# 4. Verify all tests pass
```

---

## 🔍 **DEBUGGING PATTERN**

### **WHEN TESTS FAIL:**
```bash
# 1. Run specific failing test via test.sh
./test.sh unit tests/unit/test_file.py::TestClass::test_method

# 2. Fix test, re-run, repeat
```

---

## 📝 **DOCUMENTATION HONESTY PATTERN**

### **ACTUAL VS CLAIMED STATUS:**
Always run `./test.sh` to get the real status. Never trust stale tables.

---

## 🚀 **DEPLOYMENT READINESS CHECKLIST**

### **BEFORE ANY DEPLOYMENT:**
- [ ] `./test.sh unit` — all pass
- [ ] `./test.sh integration` — all pass
- [ ] Container starts without errors
- [ ] No exceptions in container logs
- [ ] Manual verification of key workflows

**Current Status: Run `./test.sh` to verify**

---

## 💭 **PATTERN VIOLATIONS LOG**

### **What I Keep Doing Wrong:**
1. **Claiming completion without testing** ✅→❌
2. **Not rebuilding Docker container** ✅→✅ (fixed)
3. **Ignoring test failures** ✅→⚠️ (acknowledged, fixing)
4. **Creating infrastructure without implementation** ✅→⚠️ (partially)
5. **Not updating tests when changing signatures** ✅→⚠️ (in progress)

### **Accountability:**
- ✅ = Previously violated, now following pattern
- ⚠️ = Identified issue, actively fixing
- ❌ = Still violating pattern

---

---

## 🎯 **TIMEZONE HANDLING LESSONS (July 2025)**

### **LESSON 1: Timezone Should Update on Settings Change**
**Problem**: UTC offset was only calculated during task creation
**Solution**: Calculate timezone offset immediately when location is updated
```python
# BAD: Wait until task creation
def parse_task():
    offset = calculate_offset(location)  # Too late!

# GOOD: Update immediately
def update_location(location):
    offset = parse_timezone_with_llm(location)
    save_offset(offset)
```

### **LESSON 2: LLM Timezone Confusion**
**Problem**: LLM was getting confused when given timezone info
**Solution**: Timezone-agnostic approach - LLM only sees local time
```python
# BAD: "Current time: 15:30 UTC+1"
# GOOD: "Current time: 15:30" (no timezone info)
```

### **LESSON 3: Use test.sh ALWAYS**
**Problem**: Direct pytest commands miss environment setup
**Solution**: Always use `./test.sh` wrapper script
```bash
# BAD: docker-compose exec bot pytest
# BAD: python -m pytest
# GOOD: ./test.sh unit
```

---

**Last Updated**: 2025-07-17
**Deployment Ready**: Always verify with `./test.sh` before deploying