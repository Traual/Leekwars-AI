package training.fidelite;

import java.io.File;

import com.leekwars.generator.Generator;
import com.leekwars.generator.Util;
import com.leekwars.generator.outcome.Outcome;
import com.leekwars.generator.scenario.Scenario;
import com.leekwars.generator.test.LocalDbRegisterManager;
import com.leekwars.generator.test.LocalTrophyManager;
import com.leekwars.generator.util.Json;

import leekscript.compiler.LeekScript;
import leekscript.compiler.resolver.NativeFileSystem;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * Joue des scenarios de fidelite et rend la sortie COMPLETE du moteur : actions, journaux des
 * IA, vainqueur. Contrairement au runner du harnais, il lit aussi la carte personnalisee
 * (`map`), que Scenario.fromFile ne deserialise pas : c'est elle qui pose chaque entite sur la
 * cellule voulue.
 */
public final class FideliteRunner {

    private static final String PREFIX = "__FIDELITE__\t";

    private FideliteRunner() {}

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
                ObjectNode source = Json.parseObject(Util.readFile(file));
                if (source.has("fight_type")) scenario.type = source.get("fight_type").intValue();
                if (source.has("fight_context")) scenario.context = source.get("fight_context").intValue();
                if (source.has("map")) scenario.map = (ObjectNode) source.get("map");
                Outcome outcome = generator.runScenario(
                    scenario, null, new LocalDbRegisterManager(), new LocalTrophyManager());
                summary = JsonNodeFactory.instance.objectNode();
                if (outcome.exception != null) summary.put("exception", outcome.exception.toString());
                if (outcome.fight != null) summary.set("outcome", outcome.toJson());
                summary.put("winner", outcome.winner);
            } catch (Throwable error) {
                summary = JsonNodeFactory.instance.objectNode();
                summary.put("runner_error", error.toString());
            }
            System.out.println(PREFIX + index + "\t" + summary.toString());
            System.out.flush();
        }
    }
}
