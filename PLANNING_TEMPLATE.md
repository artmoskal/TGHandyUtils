# Development Planning Template

> **⚠️ IMPORTANT: DO NOT DELETE THIS TEMPLATE ⚠️**  
> This template has proven successful for complex refactoring projects.  
> It should be preserved as a reference for all future development planning.  
> Last used successfully: June 2025 for monolithic handler refactoring

> **⚠️ TEMPLATE DOCUMENT ⚠️**  
> This is an example planning document based on the successful "Version Fixing Plan" approach.  
> Use this template structure for future development planning sessions.

## 🎯 **KEY PRINCIPLES FOR EFFECTIVE PLANNING**

### **🚨 MANDATORY ANALYSIS BEFORE ANY TASK 🚨**
**BEFORE starting ANY implementation, ALWAYS:**
1. **Read existing test files** - Understand current testing patterns and infrastructure
2. **Read existing MD files** - Follow established approaches and avoid rework
3. **Check for Factory Boy usage** - Never revert to mocks if Factory Boy is available
4. **Verify architectural decisions** - Follow established DI, service, and testing patterns
5. **Document analysis findings** - Record what patterns/tools are already in use

### **Planning Philosophy:**
1. **Start with Diagnosis** - Always audit current state before making changes
2. **Prioritize by Impact** - Fix foundational issues first (FSM, DI, core architecture)
3. **Test-First MANDATORY** - Write failing tests BEFORE making any changes
4. **Test Everything** - Minimum 60% test coverage for modified code
5. **Document Progress** - Real-time status tracking with clear completion criteria
6. **Plan for Rollback** - Always have a way back if things go wrong
7. **NEVER REVERT ARCHITECTURE** - Don't undo improvements (e.g., Factory Boy → mocks)

### **Test-First Development Protocol:**
**CRITICAL**: NEVER implement changes without failing tests first
1. **Write Failing Test** - Reproduce the problem or test the new feature
2. **Verify Test Fails** - Confirm test catches the issue
3. **Implement Fix** - Make minimal changes to pass the test
4. **Verify Test Passes** - Confirm the fix works
5. **Regression Check** - Run full test suite

### **Quality Gates:**
- ✅ **Comprehensive Logging** - Debug, info, warning, error at appropriate levels
- ✅ **Error Handling** - No silent failures, graceful degradation
- ✅ **Dependency Injection** - Use DI container, never direct instantiation
- ✅ **SOLID Principles** - Single responsibility, proper abstractions
- ✅ **Test Coverage** - Minimum 60% for modified code
- ✅ **Type Hints** - All functions must have proper type annotations
- ✅ **Docstrings** - All public methods documented

### **Planning Structure:**
- **CRITICAL** sections first (foundational systems)
- **HIGH** priority (main features)
- **MEDIUM** priority (enhancements)
- **LOW** priority (cleanup, optimization)

---

## 📋 PROGRESS TRACKER TEMPLATE

### 🚀 CURRENT STATUS
**Currently Working On:** [Section Name]  
**Last Updated:** [Date]  
**Next Priority:** [Specific Task]

**✅ COMPLETED:** 
- [List completed sections with brief status]

**🚧 IN PROGRESS:**
- [Current work items]

**📋 PENDING:**
- [Upcoming work items]

---

## 🔥 SECTION TEMPLATE STRUCTURE

### **SECTION X: [PRIORITY LEVEL] - [SECTION NAME]**

#### Overview
[Brief description of what this section addresses and why it's important]

#### 📊 Diagnostic Commands
```bash
# Always start with diagnosis
echo "=== Current State ==="
[diagnostic commands to assess current state]

echo "=== Expected vs Actual ==="
[commands to compare expected vs actual behavior]
```

#### Root Cause Analysis
```
# PROBLEM: [Description of the issue]
# IMPACT: [What breaks when this doesn't work]
# CAUSE: [Why this happened]
```

#### Detailed Fix Checklist
- [ ] **X.1 Diagnostic Phase**
  - [ ] Run diagnostic commands
  - [ ] Document current state
  - [ ] Identify root causes
  
- [ ] **X.2 Implementation Phase**  
  - [ ] [Specific implementation steps]
  - [ ] [File modifications needed]
  - [ ] [Integration points to test]
  
- [ ] **X.3 Testing Phase**
  - [ ] Write unit tests (target: 60%+ coverage)
  - [ ] Write integration tests
  - [ ] Manual testing protocol
  
- [ ] **X.4 Validation Phase**
  - [ ] Performance validation
  - [ ] Error scenario testing
  - [ ] Rollback testing

#### Success Criteria
- [ ] [Specific, measurable success criteria]
- [ ] [Performance benchmarks if applicable]
- [ ] [Test coverage achieved]
- [ ] [No regressions introduced]

---

## 📊 EXAMPLE: How This Template Was Used Successfully

**Original Problem (2025-06-27):**
- Monolithic handler (1,997 lines) refactored to modular architecture
- Refactoring broke critical functionality (screenshots, voice, callbacks)
- Need systematic approach to restore functionality while preserving architecture

**Planning Approach:**
1. **Section 1: FSM State Management** (FOUNDATIONAL) - Fixed first
2. **Section 2: Screenshot Attachment** (USER-FACING) - Fixed second  
3. **Section 3: Voice Processing** (FEATURE) - Discovered already working
4. **Section 4: Callback System** (UI) - Comprehensive testing implemented

**Results:**
- ✅ All functionality restored
- ✅ Architecture improvements preserved
- ✅ 187 unit tests + 25 integration test files
- ✅ Comprehensive edge case testing (14 additional test scenarios)
- ✅ Production-ready system with full test coverage

---

## 🛠️ DIAGNOSTIC TEMPLATE COMMANDS

### Architecture Health Check
```bash
# Check current structure
find handlers/ -name "*.py" | wc -l
find services/ -name "*.py" | wc -l

# Check test coverage
docker-compose -f infra/docker-compose.test.yml run --rm bot-test python -m pytest tests/unit/ --collect-only -q | tail -3

# Check for broken imports
python -c "import handlers; import services; print('Imports OK')"
```

### Functionality Validation
```bash
# Check specific feature working
grep -n "FeatureName" handlers/ services/
docker-compose -f infra/docker-compose.test.yml run --rm bot-test python -m pytest tests/unit/test_feature.py -v

# Check integration points
grep -n "service_name" core/container.py
```

### Performance Baseline
```bash
# Get current performance metrics
time docker-compose -f infra/docker-compose.test.yml run --rm bot-test python -m pytest tests/integration/test_performance.py
```

---

## 🎯 SUCCESS METRICS TEMPLATE

### 1. **Functionality Restored:**
- [ ] [Specific feature 1] working
- [ ] [Specific feature 2] working
- [ ] [Integration points] validated

### 2. **Quality Metrics:**
- [ ] X% test coverage achieved
- [ ] No critical bugs in testing
- [ ] Performance maintained/improved
- [ ] Error rate < Y%

### 3. **Architecture Maintained:**
- [ ] Modular structure preserved
- [ ] Clean separation of concerns
- [ ] Proper dependency injection
- [ ] Testable components

---

## 🔄 ROLLBACK PLAN TEMPLATE

**If critical issues arise:**
1. **Immediate Actions:**
   ```bash
   # Restore previous working state
   git checkout [previous-working-commit] -- [critical-files]
   ```

2. **Communication:**
   - Document what went wrong
   - Notify stakeholders
   - Plan recovery approach

3. **Analysis:**
   - Root cause analysis
   - Update planning approach
   - Adjust timeline estimates

---

**Template Version:** 1.0  
**Based on:** VERSION_FIXING.md (successful example from June 2025)  
**Usage:** Copy this structure for future development planning sessions