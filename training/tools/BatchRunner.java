package training.tools;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

import com.leekwars.generator.Generator;
import com.leekwars.generator.Util;
import com.leekwars.generator.outcome.Outcome;
import com.leekwars.generator.scenario.Scenario;
import com.leekwars.generator.test.LocalDbRegisterManager;
import com.leekwars.generator.test.LocalTrophyManager;
import com.leekwars.generator.util.Json;

import leekscript.compiler.LeekScript;
import leekscript.compiler.resolver.NativeFileSystem;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/** Execute plusieurs scenarios dans une meme JVM pour reutiliser le cache de compilation. */
public final class BatchRunner {

    private static final String PREFIX = "__TRAUAL_RESULT__\t";

    private BatchRunner() {}

    public static void main(String[] args) {
        LeekScript.setFileSystem(new NativeFileSystem());
        Generator generator = new Generator();
        generator.setCache(true);

        for (int index = 0; index < args.length; index++) {
            ObjectNode summary;
            try {
                File file = new File(args[index]);
                Scenario scenario = Scenario.fromFile(file);
                if (scenario == null) throw new IllegalArgumentException("scenario illisible");
                // Scenario.fromFile ne deserialise pas ces deux champs. Le harness les porte
                // explicitement pour que getFightType/getFightContext voient le vrai mode.
                ObjectNode source = Json.parseObject(Util.readFile(file));
                if (source.has("fight_type")) scenario.type = source.get("fight_type").intValue();
                if (source.has("fight_context")) scenario.context = source.get("fight_context").intValue();
                Outcome outcome = generator.runScenario(
                    scenario, null, new LocalDbRegisterManager(), new LocalTrophyManager());
                summary = summarize(outcome);
            } catch (Throwable error) {
                summary = JsonNodeFactory.instance.objectNode();
                summary.put("runner_error", error.toString());
            }
            System.out.println(PREFIX + index + "\t" + summary.toString());
            System.out.flush();
        }
    }

    /**
     * Les erreurs SYSTEME du moteur, par entite : [entite, niveau, cle].
     *
     * Elles portent la seule information qui distingue une IA qui n'a jamais ete chargee
     * (fichier introuvable, IA invalide, compilation ratee) d'une IA qui a joue puis epuise
     * son plafond d'operations. L'action 1002 ne fait pas cette difference : elle vaut pour
     * les deux, et un combat sans IA passait donc pour un combat valide et tres rapide.
     */
    private static ArrayNode systemErrors(Outcome outcome) {
        ArrayNode found = JsonNodeFactory.instance.arrayNode();
        if (outcome.logs == null) return found;
        for (var entry : outcome.logs.entrySet()) {
            ObjectNode farmerLogs = entry.getValue().toJSON();
            for (var property : farmerLogs.properties()) {
                JsonNode group = property.getValue();
                if (group == null || !group.isArray()) continue;
                for (JsonNode raw : group) {
                    if (!raw.isArray() || raw.size() < 4) continue;
                    if (!raw.get(1).isIntegralNumber() || !raw.get(3).isIntegralNumber()) continue;
                    int level = raw.get(1).intValue();
                    if (level != 7 && level != 8) continue;   // SWARNING, SERROR
                    ArrayNode line = found.addArray();
                    line.add(raw.get(0).intValue());
                    line.add(level);
                    line.add(raw.get(3).intValue());
                }
            }
        }
        return found;
    }

    private static ObjectNode summarize(Outcome outcome) {
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        result.put("winner", outcome.winner);
        result.put("duration", outcome.duration);
        result.put("analyze_time_ns", outcome.analyzeTime);
        result.put("compilation_time_ns", outcome.compilationTime);
        result.put("execution_time_ns", outcome.executionTime);
        if (outcome.exception != null) result.put("exception", outcome.exception.toString());
        result.set("system_errors", systemErrors(outcome));
        if (outcome.fight == null) return result;

        ObjectNode fight = outcome.fight.toJSON();
        ArrayNode leeks = (ArrayNode) fight.get("leeks");
        ArrayNode actions = (ArrayNode) fight.get("actions");
        ObjectNode operations = (ObjectNode) fight.get("ops");

        Map<Integer, Integer> life = new LinkedHashMap<>();
        Map<Integer, Integer> maxLife = new LinkedHashMap<>();
        Map<Integer, ObjectNode> entityById = new LinkedHashMap<>();
        for (JsonNode raw : leeks) {
            ObjectNode entity = (ObjectNode) raw;
            int id = entity.get("id").intValue();
            int initial = entity.get("life").intValue();
            life.put(id, initial);
            maxLife.put(id, initial);
            entityById.put(id, entity);
        }

        ArrayNode aiErrors = JsonNodeFactory.instance.arrayNode();
        // Moteur 3.00 : [17, plante, declencheur, PT] ouvre un reveil, [18, plante] le ferme.
        // Les actions entre les deux sont jouees par la plante, pas par l'entite du tour.
        Map<Integer, Integer> awakenings = new LinkedHashMap<>();
        for (JsonNode raw : actions) {
            ArrayNode action = (ArrayNode) raw;
            if (action.isEmpty()) continue;
            int type = action.get(0).intValue();
            if (type == 1002) {
                aiErrors.add(action.deepCopy());
                continue;
            }
            if (type == 17 && action.size() >= 2) {
                int plant = action.get(1).intValue();
                awakenings.put(plant, awakenings.getOrDefault(plant, 0) + 1);
                continue;
            }
            if (type == 5 && action.size() >= 2) {
                life.put(action.get(1).intValue(), 0);
            } else if ((type == 101 || type == 108 || type == 109 || type == 110 || type == 111)
                    && action.size() >= 4) {
                int id = action.get(1).intValue();
                life.put(id, Math.max(0, life.getOrDefault(id, 0) - action.get(2).intValue()));
                maxLife.put(id, Math.max(1, maxLife.getOrDefault(id, 1) - action.get(3).intValue()));
            } else if (type == 107 && action.size() >= 3) {
                int id = action.get(1).intValue();
                maxLife.put(id, Math.max(1, maxLife.getOrDefault(id, 1) - action.get(2).intValue()));
            } else if (type == 103 && action.size() >= 3) {
                int id = action.get(1).intValue();
                life.put(id, Math.min(maxLife.getOrDefault(id, 1),
                    life.getOrDefault(id, 0) + action.get(2).intValue()));
            } else if (type == 104 && action.size() >= 3) {
                int id = action.get(1).intValue();
                int gain = action.get(2).intValue();
                maxLife.put(id, maxLife.getOrDefault(id, 1) + gain);
                life.put(id, life.getOrDefault(id, 0) + gain);
            } else if (type == 112 && action.size() >= 3) {
                int id = action.get(1).intValue();
                maxLife.put(id, maxLife.getOrDefault(id, 1) + action.get(2).intValue());
            } else if (type == 105 && action.size() >= 6) {
                int id = action.get(2).intValue();
                life.put(id, action.get(4).intValue());
                maxLife.put(id, action.get(5).intValue());
            }
        }

        ArrayNode entities = result.putArray("entities");
        for (var entry : entityById.entrySet()) {
            int id = entry.getKey();
            ObjectNode source = entry.getValue();
            ObjectNode entity = entities.addObject();
            entity.put("id", id);
            entity.put("team", source.get("team").intValue());
            entity.put("initial_life", source.get("life").intValue());
            entity.put("final_life", life.getOrDefault(id, 0));
            entity.put("final_max_life", maxLife.getOrDefault(id, 1));
            entity.put("summon", source.path("summon").booleanValue());
            if (operations != null && operations.has(String.valueOf(id))) {
                entity.put("operations", operations.get(String.valueOf(id)).longValue());
            } else {
                entity.put("operations", 0);
            }
        }
        result.set("ai_errors", aiErrors);
        ObjectNode plantAwakenings = result.putObject("plant_awakenings");
        for (var entry : awakenings.entrySet()) {
            plantAwakenings.put(String.valueOf(entry.getKey()), entry.getValue());
        }
        if (Boolean.getBoolean("traual.profile") || !aiErrors.isEmpty()) {
            result.set("logs", outcome.toJson().get("logs"));
        }
        return result;
    }
}
